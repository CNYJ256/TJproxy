import json
from pathlib import Path

import pytest

from tjproxy_agent.protocol import ToolCall
from tjproxy_agent.prompt import SYSTEM_PROMPT, load_system_prompt
from tjproxy_agent.runner import AgentRunner, RunOutcome, ToolDispatcher
from tjproxy_agent.workspace import Workspace


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def complete(self, messages):
        self.calls.append([dict(message) for message in messages])
        return next(self.responses)


class FakeTools:
    def __init__(self):
        self.calls = []

    def execute(self, call):
        self.calls.append(call)
        return json.dumps(
            {
                "type": "tool_result",
                "tool": call.tool,
                "ok": True,
                "stdout": "contents",
                "stderr": "",
                "exit_code": 0,
                "error_code": None,
                "truncated": False,
            }
        )


def test_tool_result_is_returned_before_next_model_call():
    client = FakeClient(
        [
            '{"type":"tool_call","tool":"read","arguments":{"path":"README.md"}}',
            '{"type":"final","content":"done"}',
        ]
    )
    tools = FakeTools()
    runner = AgentRunner(client, tools, max_rounds=32, system_prompt="protocol")

    outcome = runner.run("inspect")

    assert outcome == RunOutcome(status="completed", content="done", rounds=2)
    assert len(tools.calls) == 1
    assert "tool_result" in client.calls[1][-1]["content"]


def test_invalid_json_consumes_round_and_is_repaired():
    client = FakeClient(["not json", '{"type":"final","content":"fixed"}'])
    runner = AgentRunner(
        client, FakeTools(), max_rounds=2, system_prompt="protocol"
    )

    assert runner.run("task").content == "fixed"
    assert "protocol_error" in client.calls[1][-1]["content"]


def test_round_limit_ends_only_current_task():
    client = FakeClient(["bad", "bad", '{"type":"final","content":"recovered"}'])
    runner = AgentRunner(
        client, FakeTools(), max_rounds=2, system_prompt="protocol"
    )

    first = runner.run("task")
    second = runner.run("next")

    assert first.status == "round_limit"
    assert second.content == "recovered"


def test_clear_history_keeps_only_system_prompt():
    runner = AgentRunner(
        FakeClient(['{"type":"final","content":"x"}']),
        FakeTools(),
        max_rounds=2,
        system_prompt="protocol",
    )
    runner.run("task")

    runner.clear_history()

    assert runner.messages == [{"role": "system", "content": "protocol"}]


def test_audit_callback_observes_call_before_result():
    client = FakeClient(
        [
            '{"type":"tool_call","tool":"read","arguments":{"path":"a"}}',
            '{"type":"final","content":"done"}',
        ]
    )
    events = []
    runner = AgentRunner(
        client, FakeTools(), max_rounds=2, system_prompt="protocol", audit=events.append
    )

    runner.run("task")

    assert events[0][0] == "tool_call"
    assert events[1][0] == "tool_result"


def test_dispatcher_bounds_read_output(tmp_path: Path):
    (tmp_path / "a.txt").write_text("123456789", encoding="utf-8")
    workspace = Workspace(tmp_path, read_limit=100, write_limit=100)
    dispatcher = ToolDispatcher(workspace, powershell=None, output_chars=5)

    result = json.loads(
        dispatcher.execute(ToolCall("read", {"path": "a.txt"}))
    )

    assert result["stdout"] == "12345"
    assert result["truncated"] is True


def test_dispatcher_returns_file_failure_as_tool_result(tmp_path: Path):
    dispatcher = ToolDispatcher(
        Workspace(tmp_path, read_limit=100, write_limit=100),
        powershell=None,
        output_chars=100,
    )

    result = json.loads(
        dispatcher.execute(ToolCall("read", {"path": "missing.txt"}))
    )

    assert result["ok"] is False
    assert result["error_code"] == "NOT_FOUND"


def test_default_prompt_requires_one_json_tool_call():
    assert "exactly one JSON object" in SYSTEM_PROMPT
    assert "one tool per response" in SYSTEM_PROMPT
    assert '"type":"final"' in SYSTEM_PROMPT


def test_custom_prompt_is_relative_to_config_and_cannot_escape(tmp_path: Path):
    config_path = tmp_path / "agent.toml"
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("custom protocol", encoding="utf-8")

    assert load_system_prompt(config_path, "prompt.txt") == "custom protocol"
    with pytest.raises(ValueError, match="inside the config directory"):
        load_system_prompt(config_path, "../outside.txt")
