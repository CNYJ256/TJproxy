from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
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
                if call.tool == "powershell" and hasattr(
                    self.powershell, "approve_pipeline_once"
                ):
                    self.powershell.approve_pipeline_once(call.arguments["pipeline"])
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
        policy_profile: str = "dev",
    ):
        self.client = client
        self.tools = tools
        self.max_rounds = max_rounds
        self.system_prompt = system_prompt
        self.audit = audit or (lambda event: None)
        self.messages = [{"role": "system", "content": system_prompt}]
        self.policy_profile = policy_profile
        self.pending_approval: tuple[str, ToolCall] | None = None

    def clear_history(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.pending_approval = None
        self._clear_tool_approvals()

    def run(self, task: str) -> RunOutcome:
        history_length = len(self.messages)
        self.messages.append({"role": "user", "content": task})
        task_id = uuid4().hex
        self._set_tool_context(task_id)
        last_tool_call: ToolCall | None = None
        last_tool_result: str | None = None
        last_tool_round = 0
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
                    self._clear_tool_approvals()
                    return RunOutcome("completed", response.content, round_number)
                self.audit(("tool_call", response))
                last_tool_call = response
                result = self.tools.execute(response)
                parsed_result = _parse_tool_result(result)
                if parsed_result.get("error_code") == "APPROVAL_REQUIRED":
                    approval_id = parsed_result.get("metadata", {}).get("approval_id")
                    if isinstance(approval_id, str):
                        self.pending_approval = (approval_id, response)
                    return RunOutcome("approval_required", result, round_number)
                self.audit(("tool_result", result))
                self.messages.append({"role": "user", "content": result})
                last_tool_result = result
                last_tool_round = round_number
            self._clear_tool_approvals()
            return RunOutcome(
                "round_limit", "maximum agent rounds reached", self.max_rounds
            )
        except BaseException as exc:
            self._clear_tool_approvals()
            if (
                last_tool_call is not None
                and last_tool_result is not None
                and not isinstance(exc, KeyboardInterrupt)
            ):
                return RunOutcome(
                    "incomplete",
                    _incomplete_after_tool_message(last_tool_call, last_tool_result, exc),
                    last_tool_round,
                )
            del self.messages[history_length:]
            raise

    def _set_tool_context(self, task_id: str) -> None:
        if not hasattr(self.tools, "set_policy_context"):
            return
        from .policy import PolicyContext

        workspace = getattr(getattr(self.tools, "workspace", None), "root", None)
        if workspace is None:
            return
        self.tools.set_policy_context(
            PolicyContext(
                profile=self.policy_profile,
                task_id=task_id,
                workspace=workspace,
            )
        )

    def _clear_tool_approvals(self) -> None:
        if hasattr(self.tools, "clear_approvals"):
            self.tools.clear_approvals()

    def approve_pending(self, approval_id: str) -> RunOutcome:
        if self.pending_approval is None or self.pending_approval[0] != approval_id:
            return RunOutcome("completed", "没有待批准的操作", 0)
        _, call = self.pending_approval
        self.pending_approval = None
        if hasattr(self.tools, "approve_once"):
            self.tools.approve_once(approval_id)
        result = self.tools.execute(call)
        self.audit(("tool_result", result))
        self.messages.append({"role": "user", "content": result})
        return RunOutcome("completed", result, 1)

    def reject_pending(self, approval_id: str) -> RunOutcome:
        if self.pending_approval is not None and self.pending_approval[0] == approval_id:
            self.pending_approval = None
            self._clear_tool_approvals()
        denial = tool_result_message(
            "approval",
            ok=False,
            stderr="用户拒绝了该操作",
            error_code="POLICY_DENIED_BY_USER",
            metadata={"approval_id": approval_id},
        )
        self.messages.append({"role": "user", "content": denial})
        return RunOutcome("completed", "已拒绝", 0)


def _parse_tool_result(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _incomplete_after_tool_message(
    call: ToolCall, result: str, exc: BaseException
) -> str:
    parsed = _parse_tool_result(result)
    status = (
        f"ok={parsed.get('ok')} error={parsed.get('error_code')}"
        if parsed
        else "工具结果无法解析"
    )
    return (
        "任务未完成："
        f"{_tool_call_summary(call)} 已返回工具结果（{status}），"
        f"但模型回复中断：{exc}"
    )


def _tool_call_summary(call: ToolCall) -> str:
    if call.tool in {"read", "list_dir", "write", "edit"}:
        path = call.arguments.get("path")
        return f"{call.tool} {path}" if isinstance(path, str) else call.tool
    if call.tool == "read_range":
        return (
            f"read_range {call.arguments.get('path')} "
            f"L{call.arguments.get('start')}-L{call.arguments.get('end')}"
        )
    if call.tool == "search":
        return (
            f"search {call.arguments.get('path')} "
            f"query={call.arguments.get('query')!r}"
        )
    if call.tool == "context_pack":
        paths = call.arguments.get("paths", [])
        return (
            f"context_pack {', '.join(paths)}"
            if isinstance(paths, list)
            else "context_pack"
        )
    if call.tool == "powershell":
        commands = [
            stage.get("command", "?")
            for stage in call.arguments.get("pipeline", [])
            if isinstance(stage, dict)
        ]
        return f"powershell {' | '.join(commands)}"
    return call.tool
