from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .powershell import ShellFailure
from .protocol import (
    FinalResponse,
    ProtocolError,
    ToolCall,
    parse_response,
    protocol_error_message,
    tool_result_message,
)
from .workspace import ToolFailure, Workspace


@dataclass(frozen=True)
class RunOutcome:
    status: str
    content: str
    rounds: int


class ToolDispatcher:
    def __init__(
        self,
        workspace: Workspace,
        powershell: Any,
        *,
        output_chars: int = 20_000,
        policy_engine: Any | None = None,
        approval_store: Any | None = None,
    ):
        self.workspace = workspace
        self.powershell = powershell
        self.output_chars = output_chars
        self.policy_engine = policy_engine
        self.approval_store = approval_store
        self.policy_context = None
        self._approved_once: set[str] = set()

    def execute(self, call: ToolCall) -> str:
        policy_result = self._review_policy(call)
        if policy_result is not None:
            return policy_result
        try:
            if call.tool == "read":
                content = self.workspace.read(call.arguments["path"])
                stdout, truncated = self._bounded(self._number_lines(content))
                return tool_result_message(
                    "read", ok=True, stdout=stdout, truncated=truncated
                )
            if call.tool == "list_dir":
                stdout, truncated = self._bounded(
                    self.workspace.list_dir(call.arguments["path"])
                )
                return tool_result_message(
                    "list_dir", ok=True, stdout=stdout, truncated=truncated
                )
            if call.tool == "read_range":
                stdout, truncated = self._bounded(
                    self.workspace.read_range(
                        call.arguments["path"],
                        call.arguments["start"],
                        call.arguments["end"],
                    )
                )
                return tool_result_message(
                    "read_range", ok=True, stdout=stdout, truncated=truncated
                )
            if call.tool == "search":
                stdout, truncated = self._bounded(
                    self.workspace.search(
                        call.arguments["query"], call.arguments["path"]
                    )
                )
                return tool_result_message(
                    "search", ok=True, stdout=stdout, truncated=truncated
                )
            if call.tool == "project_map":
                stdout, truncated = self._bounded(self.workspace.project_map())
                return tool_result_message(
                    "project_map", ok=True, stdout=stdout, truncated=truncated
                )
            if call.tool == "context_pack":
                stdout, truncated = self._bounded(
                    self.workspace.context_pack(
                        call.arguments["paths"], call.arguments["query"]
                    )
                )
                return tool_result_message(
                    "context_pack", ok=True, stdout=stdout, truncated=truncated
                )
            if call.tool == "write":
                self.workspace.write(
                    call.arguments["path"], call.arguments["content"]
                )
                return tool_result_message("write", ok=True)
            if call.tool == "edit":
                count = self.workspace.edit(
                    call.arguments["path"],
                    call.arguments["old_text"],
                    call.arguments["new_text"],
                    expected_replacements=call.arguments.get(
                        "expected_replacements", 1
                    ),
                )
                return tool_result_message(
                    "edit", ok=True, stdout=f"replacements={count}"
                )
            if call.tool != "powershell" or self.powershell is None:
                return tool_result_message(
                    call.tool,
                    ok=False,
                    stderr="tool is unavailable",
                    error_code="TOOL_UNAVAILABLE",
                )
            result = self.powershell.run(call.arguments["pipeline"])
            return tool_result_message(
                "powershell",
                ok=result.exit_code == 0 and not result.timed_out,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                error_code=result.error_code,
                truncated=result.truncated,
            )
        except (ToolFailure, ShellFailure) as exc:
            return tool_result_message(
                call.tool,
                ok=False,
                error_code=exc.code,
                stderr=exc.message,
            )

    def set_policy_context(self, context: Any) -> None:
        self.policy_context = context

    def approve_once(self, approval_id: str) -> None:
        self._approved_once.add(approval_id)

    def clear_approvals(self) -> None:
        self._approved_once.clear()
        if self.approval_store is not None and self.policy_context is not None:
            self.approval_store.clear_task(self.policy_context.task_id)

    def _review_policy(self, call: ToolCall) -> str | None:
        if self.policy_engine is None or self.policy_context is None:
            return None
        if self.approval_store is None:
            raise RuntimeError("approval_store is required when policy_engine is set")
        from .policy import PolicyDecision

        for approval_id in list(self._approved_once):
            if self.approval_store.consume(approval_id, call, self.policy_context):
                self._approved_once.remove(approval_id)
                return None
        review = self.policy_engine.review(call, self.policy_context)
        if review.kind == PolicyDecision.ALLOW:
            return None
        if review.kind == PolicyDecision.DENY:
            return tool_result_message(
                call.tool,
                ok=False,
                stderr=review.reason,
                error_code=review.error_code or "POLICY_DENIED",
            )
        approval = self.approval_store.create(review, call, self.policy_context)
        return tool_result_message(
            call.tool,
            ok=False,
            stdout=approval.summary,
            stderr=approval.reason,
            error_code="APPROVAL_REQUIRED",
            metadata={
                "approval_id": approval.approval_id,
                "risk": approval.risk.value if approval.risk else None,
                "summary": approval.summary,
                "reason": approval.reason,
                "details": approval.details,
            },
        )

    def _bounded(self, value: str) -> tuple[str, bool]:
        if len(value) <= self.output_chars:
            return value, False
        return value[: self.output_chars], True

    def _number_lines(self, value: str) -> str:
        return "\n".join(
            f"{line_number} | {line}"
            for line_number, line in enumerate(value.splitlines(), 1)
        )


AuditCallback = Callable[[tuple[str, object]], None]


class AgentRunner:
    def __init__(
        self,
        client: Any,
        tools: Any,
        *,
        max_rounds: int,
        system_prompt: str,
        audit: AuditCallback | None = None,
    ):
        self.client = client
        self.tools = tools
        self.max_rounds = max_rounds
        self.system_prompt = system_prompt
        self.audit = audit or (lambda event: None)
        self.messages = [{"role": "system", "content": system_prompt}]

    def clear_history(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def run(self, task: str) -> RunOutcome:
        history_length = len(self.messages)
        self.messages.append({"role": "user", "content": task})
        try:
            for round_number in range(1, self.max_rounds + 1):
                raw = self.client.complete(self.messages)
                self.messages.append({"role": "assistant", "content": raw})
                try:
                    response = parse_response(raw)
                except ProtocolError as exc:
                    self.messages.append(
                        {
                            "role": "user",
                            "content": protocol_error_message(str(exc)),
                        }
                    )
                    continue
                if isinstance(response, FinalResponse):
                    return RunOutcome("completed", response.content, round_number)
                self.audit(("tool_call", response))
                result = self.tools.execute(response)
                self.audit(("tool_result", result))
                self.messages.append({"role": "user", "content": result})
            return RunOutcome(
                "round_limit", "maximum agent rounds reached", self.max_rounds
            )
        except BaseException:
            del self.messages[history_length:]
            raise
