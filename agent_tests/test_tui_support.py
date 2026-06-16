import json
from pathlib import Path

from tjproxy_agent.protocol import ToolCall, tool_result_message
from tjproxy_agent.runner import RunOutcome
from tjproxy_agent.tui_support import (
    AgentUiState,
    CommandResult,
    PlanModeToolDispatcher,
    TuiEventFormatter,
    handle_slash_command,
    prepare_task,
)


class FakeTools:
    def __init__(self):
        self.calls = []

    def execute(self, call):
        self.calls.append(call)
        return tool_result_message(call.tool, ok=True, stdout="ok")


class FakeRunner:
    def __init__(self):
        self.clears = 0

    def clear_history(self):
        self.clears += 1


def test_plan_mode_allows_only_plan_file_writes_and_blocks_powershell():
    tools = FakeTools()
    guarded = PlanModeToolDispatcher(tools, plan_mode=True)

    allowed = json.loads(
        guarded.execute(
            ToolCall(
                "write",
                {"path": "docs/plan/agent-tui-plan.md", "content": "plan"},
            )
        )
    )
    blocked_write = json.loads(
        guarded.execute(
            ToolCall("write", {"path": "src/app.py", "content": "code"})
        )
    )
    blocked_shell = json.loads(
        guarded.execute(
            ToolCall("powershell", {"pipeline": [{"command": "git", "args": ["status"]}]})
        )
    )

    assert allowed["ok"] is True
    assert blocked_write["ok"] is False
    assert blocked_write["error_code"] == "PLAN_MODE_BLOCKED"
    assert blocked_shell["ok"] is False
    assert [call.tool for call in tools.calls] == ["write"]


def test_plan_mode_allows_local_exploration_tools():
    tools = FakeTools()
    guarded = PlanModeToolDispatcher(tools, plan_mode=True)

    result = json.loads(
        guarded.execute(ToolCall("search", {"query": "AgentRunner", "path": "."}))
    )

    assert result["ok"] is True
    assert tools.calls[0].tool == "search"


def test_default_mode_does_not_restrict_tool_calls():
    tools = FakeTools()
    guarded = PlanModeToolDispatcher(tools, plan_mode=False)

    result = json.loads(
        guarded.execute(ToolCall("write", {"path": "src/app.py", "content": "code"}))
    )

    assert result["ok"] is True
    assert tools.calls[0].arguments["path"] == "src/app.py"


def test_prepare_task_adds_plan_mode_instruction_only_in_plan_mode():
    normal = prepare_task("构建它", plan_mode=False)
    planned = prepare_task("构建它", plan_mode=True)

    assert normal == "构建它"
    assert "计划模式" in planned
    assert "docs/plan/" in planned
    assert "构建它" in planned


def test_slash_commands_update_ui_state_and_runner():
    runner = FakeRunner()
    state = AgentUiState(workspace=Path("D:/repo"), mode="default", rounds=2)

    assert handle_slash_command("/status", state, runner).kind == "message"
    assert state.mode == "default"

    mode_result = handle_slash_command("/plan", state, runner)
    assert mode_result == CommandResult("message", "已进入计划模式")
    assert state.mode == "plan"

    reset_result = handle_slash_command("/reset", state, runner)
    assert reset_result == CommandResult("message", "会话上下文已重置")
    assert runner.clears == 1

    copy_result = handle_slash_command("/copy", state, runner)
    assert copy_result.kind == "copy_output"

    clear_result = handle_slash_command("/clear", state, runner)
    assert clear_result.kind == "clear"

    exit_result = handle_slash_command("/exit", state, runner)
    assert exit_result.kind == "exit"


def test_help_and_status_messages_are_chinese_while_commands_stay_english():
    runner = FakeRunner()
    state = AgentUiState(workspace=Path("D:/repo"), mode="default", rounds=2)

    help_result = handle_slash_command("/help", state, runner)
    status_result = handle_slash_command("/status", state, runner)

    assert "/help" in help_result.message
    assert "/copy" in help_result.message
    assert "显示帮助" in help_result.message
    assert "工作区" in status_result.message
    assert "模式=default" in status_result.message


def test_event_formatter_labels_tool_calls_results_and_final():
    formatter = TuiEventFormatter()

    tool_line = formatter.format_audit(
        ("tool_call", ToolCall("read_range", {"path": "a.py", "start": 1, "end": 3}))
    )
    result_line = formatter.format_audit(
        (
            "tool_result",
            tool_result_message(
                "read_range", ok=True, stdout="1 | alpha", truncated=False
            ),
        )
    )
    final_line = formatter.format_outcome(RunOutcome("completed", "done", 3))

    assert "tool_call" in tool_line.plain
    assert "read_range" in tool_line.plain
    assert "tool_result" in result_line.plain
    assert "1 | alpha" in result_line.plain
    assert "final" in final_line.plain
    assert "rounds=3" in final_line.plain
