from pathlib import Path

import pytest

from tjproxy_agent.cli import build_parser
from tjproxy_agent.protocol import ToolCall, tool_result_message
from tjproxy_agent.runner import RunOutcome
from tjproxy_agent.tui import AgentTuiApp


class FakeRunner:
    def __init__(self):
        self.audit = None
        self.tasks = []

    def clear_history(self):
        pass

    def run(self, task):
        self.tasks.append(task)
        return RunOutcome("completed", "ok", 1)


class FakeGuardedTools:
    plan_mode = False


def test_tui_app_exposes_required_key_bindings():
    app = AgentTuiApp(FakeRunner(), FakeGuardedTools(), workspace=Path("D:/repo"))
    bindings = {binding.key: binding.action for binding in app.BINDINGS}

    assert bindings["ctrl+c"] == "interrupt"
    assert bindings["ctrl+d"] == "quit"
    assert bindings["ctrl+enter"] == "submit"
    assert bindings["f10"] == "submit"
    assert bindings["ctrl+s"] == "submit"
    assert bindings["ctrl+v"] == "paste_to_input"
    assert bindings["f4"] == "paste_to_input"
    assert bindings["f9"] == "copy_output"


def test_tui_app_starts_in_default_mode_with_workspace_status():
    app = AgentTuiApp(FakeRunner(), FakeGuardedTools(), workspace=Path("D:/repo"))

    assert app.state.mode == "default"
    assert "D:" in app.status_text
    assert "repo" in app.status_text
    assert "模式: default" in app.status_text
    assert "提交" in app.status_text


def test_parser_supports_plain_cli_fallback():
    args = build_parser().parse_args(["--workspace", "D:/repo", "--plain"])

    assert args.plain is True


@pytest.mark.asyncio
async def test_tui_app_composes_required_widgets():
    app = AgentTuiApp(FakeRunner(), FakeGuardedTools(), workspace=Path("D:/repo"))

    async with app.run_test():
        assert app.query_one("#log")
        assert app.query_one("#input")
        assert app.query_one("#status-panel")


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["ctrl+enter", "f10", "ctrl+s"])
@pytest.mark.asyncio
async def test_submit_keys_submit_multiline_input_to_runner(key):
    runner = FakeRunner()
    app = AgentTuiApp(runner, FakeGuardedTools(), workspace=Path("D:/repo"))

    async with app.run_test() as pilot:
        app.query_one("#input").load_text("hello\nworld")
        await pilot.press(key)
        await pilot.pause(0.5)

    assert runner.tasks == ["hello\nworld"]


@pytest.mark.asyncio
async def test_copy_command_copies_plain_output_transcript():
    app = AgentTuiApp(FakeRunner(), FakeGuardedTools(), workspace=Path("D:/repo"))

    async with app.run_test() as pilot:
        app._write_log("[blue]彩色[/blue] 输出", plain="彩色 输出")
        app.query_one("#input").load_text("/copy")
        await pilot.press("f10")
        await pilot.pause(0.2)

    assert "彩色 输出" in app.clipboard


@pytest.mark.asyncio
async def test_tool_call_and_result_update_one_compact_block():
    app = AgentTuiApp(FakeRunner(), FakeGuardedTools(), workspace=Path("D:/repo"))

    async with app.run_test() as pilot:
        app._append_audit(
            0,
            ("tool_call", ToolCall("read", {"path": "test_write.txt"})),
        )
        await pilot.pause(0.1)
        tool_blocks = list(app.query(".tool-block"))
        assert len(tool_blocks) == 1
        assert "read test_write.txt" in tool_blocks[0].plain_text
        assert "等待结果" in tool_blocks[0].plain_text

        app._append_audit(
            0,
            (
                "tool_result",
                tool_result_message(
                    "read",
                    ok=True,
                    stdout="1 | 测试文件写入成功",
                    truncated=False,
                ),
            ),
        )
        await pilot.pause(0.1)
        tool_blocks = list(app.query(".tool-block"))

    assert len(tool_blocks) == 1
    assert "ok=True" in tool_blocks[0].plain_text
    assert "1 | 测试文件写入成功" in tool_blocks[0].plain_text


@pytest.mark.asyncio
async def test_multiple_tool_calls_refresh_the_same_tool_block():
    app = AgentTuiApp(FakeRunner(), FakeGuardedTools(), workspace=Path("D:/repo"))

    async with app.run_test() as pilot:
        app._append_audit(0, ("tool_call", ToolCall("write", {"path": "a.txt", "content": "x"})))
        app._append_audit(0, ("tool_result", tool_result_message("write", ok=True)))
        app._append_audit(0, ("tool_call", ToolCall("read", {"path": "a.txt"})))
        app._append_audit(
            0,
            (
                "tool_result",
                tool_result_message("read", ok=True, stdout="1 | x"),
            ),
        )
        await pilot.pause(0.1)
        tool_blocks = list(app.query(".tool-block"))

    assert len(tool_blocks) == 1
    assert "1. tool_call write a.txt" in tool_blocks[0].plain_text
    assert "2. tool_call read a.txt" in tool_blocks[0].plain_text
    assert "1 | x" in tool_blocks[0].plain_text
