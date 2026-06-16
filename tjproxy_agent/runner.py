from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
    ):
        self.workspace = workspace
        self.powershell = powershell
        self.output_chars = output_chars

    def execute(self, call: ToolCall) -> str:
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
