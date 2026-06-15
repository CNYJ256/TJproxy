from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


class ProtocolError(ValueError):
    """Raised when model output does not match the tool protocol."""


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class FinalResponse:
    content: str


ALLOWED_TOOLS = {"read", "write", "edit", "powershell"}
ARGUMENT_KEYS = {
    "read": ({"path"}, {"path"}),
    "write": ({"path", "content"}, {"path", "content"}),
    "edit": (
        {"path", "old_text", "new_text"},
        {"path", "old_text", "new_text", "expected_replacements"},
    ),
    "powershell": ({"pipeline"}, {"pipeline"}),
}


def parse_response(text: str) -> ToolCall | FinalResponse:
    candidate = _strip_one_json_fence(text.strip())
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("response must be one JSON object")

    response_type = value.get("type")
    if response_type == "final":
        _require_exact_keys(value, {"type", "content"}, "final")
        if not isinstance(value["content"], str):
            raise ProtocolError("final.content must be a string")
        return FinalResponse(value["content"])
    if response_type != "tool_call":
        raise ProtocolError("type must be tool_call or final")

    _require_exact_keys(value, {"type", "tool", "arguments"}, "tool_call")
    tool = value["tool"]
    if tool not in ALLOWED_TOOLS:
        raise ProtocolError(f"unknown tool: {tool}")
    arguments = value["arguments"]
    if not isinstance(arguments, dict):
        raise ProtocolError(f"{tool}.arguments must be an object")
    _validate_arguments(tool, arguments)
    return ToolCall(tool, arguments)


def _strip_one_json_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if (
        len(lines) < 3
        or lines[0] not in ("```", "```json")
        or lines[-1] != "```"
    ):
        raise ProtocolError("invalid JSON fence")
    return "\n".join(lines[1:-1]).strip()


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ProtocolError(
            f"{label} keys must be {sorted(expected)}; got {sorted(actual)}"
        )


def _validate_arguments(tool: str, arguments: dict[str, Any]) -> None:
    required, allowed = ARGUMENT_KEYS[tool]
    actual = set(arguments)
    if not required <= actual or not actual <= allowed:
        raise ProtocolError(f"{tool}.arguments has invalid keys")

    if tool == "read":
        _require_string(arguments["path"], "read.arguments.path")
        return
    if tool == "write":
        _require_string(arguments["path"], "write.arguments.path")
        _require_string(arguments["content"], "write.arguments.content", allow_empty=True)
        return
    if tool == "edit":
        _require_string(arguments["path"], "edit.arguments.path")
        _require_string(arguments["old_text"], "edit.arguments.old_text")
        _require_string(
            arguments["new_text"], "edit.arguments.new_text", allow_empty=True
        )
        count = arguments.get("expected_replacements", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ProtocolError(
                "edit.arguments.expected_replacements must be a positive integer"
            )
        return

    pipeline = arguments["pipeline"]
    if not isinstance(pipeline, list) or not pipeline:
        raise ProtocolError(
            "powershell.arguments.pipeline must be a non-empty array"
        )
    for index, stage in enumerate(pipeline):
        if not isinstance(stage, dict) or set(stage) != {"command", "args"}:
            raise ProtocolError(f"powershell stage {index} has invalid keys")
        _require_string(stage["command"], f"powershell stage {index} command")
        args = stage["args"]
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ProtocolError(f"powershell stage {index} args must be strings")


def _require_string(value: Any, label: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ProtocolError(f"{label} must be a string")


def protocol_error_message(message: str) -> str:
    return json.dumps(
        {"type": "protocol_error", "error": message}, ensure_ascii=False
    )


def tool_result_message(
    tool: str,
    *,
    ok: bool,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    error_code: str | None = None,
    truncated: bool = False,
) -> str:
    return json.dumps(
        {
            "type": "tool_result",
            "tool": tool,
            "ok": ok,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "error_code": error_code,
            "truncated": truncated,
        },
        ensure_ascii=False,
    )
