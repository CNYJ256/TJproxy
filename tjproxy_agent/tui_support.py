from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PureWindowsPath
from typing import Any

from rich.text import Text

from .protocol import ToolCall, tool_result_message
from .runner import RunOutcome

EXPLORATION_TOOLS = {
    "read",
    "list_dir",
    "read_range",
    "search",
    "project_map",
    "context_pack",
}


@dataclass
class AgentUiState:
    workspace: Path
    mode: str = "default"
    rounds: int = 0
    running: bool = False


@dataclass(frozen=True)
class CommandResult:
    kind: str
    message: str = ""


# Compatibility wrapper for existing tests; policy profiles own plan mode now.
class PlanModeToolDispatcher:
    def __init__(self, tools: Any, *, plan_mode: bool = False):
        self.tools = tools
        self.plan_mode = plan_mode

    def execute(self, call: ToolCall) -> str:
        if not self.plan_mode:
            return self.tools.execute(call)
        if call.tool in EXPLORATION_TOOLS:
            return self.tools.execute(call)
        if call.tool in {"write", "edit"} and _is_plan_path(call.arguments["path"]):
            return self.tools.execute(call)
        return tool_result_message(
            call.tool,
            ok=False,
            stderr=(
                "计划模式已阻止代码修改；只允许本地探索工具，以及写入或编辑 "
                "docs/plan/*plan.md"
            ),
            error_code="PLAN_MODE_BLOCKED",
        )


class TuiEventFormatter:
    def format_audit(self, event: tuple[str, object]) -> Text:
        kind, value = event
        if kind == "tool_call" and isinstance(value, ToolCall):
            line = Text("tool_call ", style="bold cyan")
            line.append(_tool_call_summary(value), style="bright_blue")
            return line
        if kind == "tool_result" and isinstance(value, str):
            return self._format_tool_result(value)
        return Text(str(event), style="dim")

    def format_outcome(self, outcome: RunOutcome) -> Text:
        label = "final" if outcome.status == "completed" else outcome.status
        line = Text(f"{label} ", style="bold green" if outcome.status == "completed" else "bold yellow")
        line.append(f"rounds={outcome.rounds}\n", style="blue")
        line.append(outcome.content, style="white")
        return line

    def _format_tool_result(self, raw: str) -> Text:
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            line = Text("tool_result ", style="bold magenta")
            line.append("invalid result envelope", style="red")
            return line
        ok = result.get("ok")
        line = Text("tool_result ", style="bold magenta")
        line.append(
            f"{result.get('tool')} ok={ok} "
            f"exit={result.get('exit_code')} "
            f"error={result.get('error_code')} "
            f"truncated={result.get('truncated')}",
            style="green" if ok else "red",
        )
        stdout = result.get("stdout")
        stderr = result.get("stderr")
        if isinstance(stdout, str) and stdout:
            line.append("\n")
            line.append(stdout[:800], style="white")
        if isinstance(stderr, str) and stderr:
            line.append("\n")
            line.append(stderr[:800], style="red")
        return line


def prepare_task(task: str, *, plan_mode: bool) -> str:
    if not plan_mode:
        return task
    return (
        "计划模式：不要编写或修改应用代码，不要运行 shell/PowerShell 命令。"
        "可以按需使用本地探索工具读取项目。若需要持久化输出，只能在工作区内"
        "创建或编辑 docs/plan/<name>plan.md。\n\n"
        f"用户请求：\n{task}"
    )


def handle_slash_command(command: str, state: AgentUiState, runner: Any) -> CommandResult:
    normalized = command.strip()
    if normalized == "/help":
        return CommandResult("message", _help_text())
    if normalized == "/exit":
        return CommandResult("exit", "再见")
    if normalized == "/clear":
        return CommandResult("clear", "已清空屏幕")
    if normalized == "/copy":
        return CommandResult("copy_output", "复制输出")
    if normalized in {"/reset", "/new"}:
        runner.clear_history()
        state.rounds = 0
        return CommandResult("message", "会话上下文已重置")
    if normalized == "/status":
        return CommandResult(
            "message",
            (
                f"工作区={state.workspace} 模式={state.mode} "
                f"累计轮数={state.rounds} 运行中={state.running}"
            ),
        )
    if normalized == "/plan":
        state.mode = "plan"
        return CommandResult("message", "已进入计划模式")
    if normalized == "/default":
        state.mode = "default"
        return CommandResult("message", "已进入默认模式")
    return CommandResult("message", f"未知命令：{normalized}")


def _help_text() -> str:
    return "\n".join(
        [
            "/help     显示帮助",
            "/exit     退出 TUI",
            "/clear    清空可见输出",
            "/copy     复制完整输出到剪贴板",
            "/status   显示工作区、模式、轮数和运行状态",
            "/reset    重置模型会话上下文",
            "/plan     进入计划模式",
            "/default  进入默认模式",
            "Enter     输入换行",
            "F10       提交输入（推荐，兼容性最好）",
            "Ctrl+S    提交输入",
            "Ctrl+Enter 提交输入（终端支持时可用）",
            "F4/Ctrl+V 粘贴到输入框",
            "F9        复制完整输出到剪贴板",
            "Ctrl+C    中断当前任务",
            "Ctrl+D    退出 TUI",
        ]
    )


def _is_plan_path(raw_path: str) -> bool:
    path = PureWindowsPath(raw_path)
    parts = tuple(part.lower() for part in path.parts)
    if len(parts) < 3:
        return False
    if parts[0] != "docs" or parts[1] != "plan":
        return False
    return parts[-1].endswith("plan.md")


def _tool_call_summary(call: ToolCall) -> str:
    if call.tool == "read":
        return f"read {call.arguments['path']}"
    if call.tool == "list_dir":
        return f"list_dir {call.arguments['path']}"
    if call.tool == "read_range":
        return (
            f"read_range {call.arguments['path']} "
            f"L{call.arguments['start']}-L{call.arguments['end']}"
        )
    if call.tool == "search":
        return f"search {call.arguments['path']} query={call.arguments['query']!r}"
    if call.tool == "project_map":
        return "project_map"
    if call.tool == "context_pack":
        return f"context_pack {', '.join(call.arguments['paths'])}"
    if call.tool == "write":
        return (
            f"write {call.arguments['path']} "
            f"({len(call.arguments['content'])} chars)"
        )
    if call.tool == "edit":
        return f"edit {call.arguments['path']}"
    commands = [
        stage.get("command", "?") for stage in call.arguments.get("pipeline", [])
    ]
    return f"powershell {' | '.join(commands)}"
