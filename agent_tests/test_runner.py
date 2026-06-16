import json
from pathlib import Path

import pytest

from tjproxy_agent.client import ServiceError
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
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


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


def test_clear_history_clears_pending_approval_and_tool_store():
    class ApprovalTools(FakeTools):
        def __init__(self):
            super().__init__()
            self.clears = 0

        def clear_approvals(self):
            self.clears += 1

    tools = ApprovalTools()
    runner = AgentRunner(
        FakeClient(['{"type":"final","content":"x"}']),
        tools,
        max_rounds=2,
        system_prompt="protocol",
    )
    runner.pending_approval = ("approval-1", ToolCall("read", {"path": "a.txt"}))

    runner.clear_history()

    assert runner.pending_approval is None
    assert tools.clears == 1


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

    assert result["stdout"] == "1 | 1"
    assert result["truncated"] is True


def test_dispatcher_formats_read_with_line_numbers(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha\nbeta", encoding="utf-8")
    workspace = Workspace(tmp_path, read_limit=100, write_limit=100)
    dispatcher = ToolDispatcher(workspace, powershell=None, output_chars=100)

    result = json.loads(
        dispatcher.execute(ToolCall("read", {"path": "a.txt"}))
    )

    assert result["stdout"] == "1 | alpha\n2 | beta"


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        ("list_dir", {"path": "."}, "a.txt"),
        ("read_range", {"path": "a.txt", "start": 1, "end": 1}, "1 | needle"),
        ("search", {"query": "needle", "path": "."}, "a.txt:1 | needle"),
        ("project_map", {}, "a.txt"),
        (
            "context_pack",
            {"paths": ["a.txt"], "query": "needle"},
            "# a.txt",
        ),
    ],
)
def test_dispatcher_runs_local_exploration_tools(tmp_path: Path, tool, arguments, expected):
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    workspace = Workspace(tmp_path, read_limit=1000, write_limit=1000)
    dispatcher = ToolDispatcher(workspace, powershell=None, output_chars=1000)

    result = json.loads(dispatcher.execute(ToolCall(tool, arguments)))

    assert result["ok"] is True
    assert expected in result["stdout"]


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


@pytest.mark.parametrize(
    "failure", [ServiceError("offline"), KeyboardInterrupt()]
)
def test_interrupted_model_request_rolls_back_current_task(failure):
    client = FakeClient(
        ['{"type":"final","content":"first"}', failure]
    )
    runner = AgentRunner(
        client, FakeTools(), max_rounds=2, system_prompt="protocol"
    )
    runner.run("completed task")
    history_before = list(runner.messages)

    with pytest.raises(type(failure)):
        runner.run("failed task")

    assert runner.messages == history_before


def test_model_failure_after_tool_result_returns_incomplete_and_keeps_context():
    client = FakeClient(
        [
            '{"type":"tool_call","tool":"write","arguments":{"path":"todo.py","content":"print(1)"}}',
            ServiceError("upstream timed out"),
        ]
    )
    runner = AgentRunner(
        client,
        FakeTools(),
        max_rounds=2,
        system_prompt="protocol",
    )

    outcome = runner.run("create todo tool")

    assert outcome.status == "incomplete"
    assert outcome.rounds == 1
    assert "模型回复中断" in outcome.content
    assert "write todo.py" in outcome.content
    assert "upstream timed out" in outcome.content
    assert runner.messages[-1]["role"] == "user"
    assert "tool_result" in runner.messages[-1]["content"]


def test_model_failure_before_any_tool_result_still_rolls_back_current_task():
    client = FakeClient(
        [
            '{"type":"final","content":"first"}',
            ServiceError("offline before tool"),
        ]
    )
    runner = AgentRunner(
        client,
        FakeTools(),
        max_rounds=2,
        system_prompt="protocol",
    )
    runner.run("completed task")
    history_before = list(runner.messages)

    with pytest.raises(ServiceError):
        runner.run("failed task")

    assert runner.messages == history_before


# ── Task 4: dispatcher policy gate ──────────────────────────────────────────

from tjproxy_agent.policy import ApprovalStore, PolicyContext, PolicyEngine, load_policy_config


def test_dispatcher_returns_approval_required_for_dangerous_command(tmp_path: Path):
    workspace = Workspace(tmp_path, read_limit=1000, write_limit=1000)
    policy = PolicyEngine(load_policy_config(tmp_path / "missing.toml"))
    approvals = ApprovalStore()
    dispatcher = ToolDispatcher(
        workspace,
        powershell=None,
        output_chars=1000,
        policy_engine=policy,
        approval_store=approvals,
    )
    dispatcher.set_policy_context(
        PolicyContext(profile="dev", task_id="task-1", workspace=tmp_path)
    )

    result = json.loads(
        dispatcher.execute(
            ToolCall(
                "powershell",
                {"pipeline": [{"command": "git", "args": ["reset", "--hard"]}]},
            )
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "APPROVAL_REQUIRED"
    assert result["metadata"]["risk"] == "vcs_destructive"
    assert result["metadata"]["approval_id"]


def test_dispatcher_consumes_approval_and_executes_once(tmp_path: Path):
    class FakePowerShell:
        def __init__(self):
            self.calls = []

        def run(self, pipeline):
            self.calls.append(pipeline)
            from tjproxy_agent.powershell import ShellResult

            return ShellResult(0, "ran", "")

    workspace = Workspace(tmp_path, read_limit=1000, write_limit=1000)
    shell = FakePowerShell()
    policy = PolicyEngine(load_policy_config(tmp_path / "missing.toml"))
    approvals = ApprovalStore()
    dispatcher = ToolDispatcher(
        workspace,
        powershell=shell,
        output_chars=1000,
        policy_engine=policy,
        approval_store=approvals,
    )
    context = PolicyContext(profile="dev", task_id="task-1", workspace=tmp_path)
    dispatcher.set_policy_context(context)
    call = ToolCall(
        "powershell",
        {"pipeline": [{"command": "git", "args": ["reset", "--hard"]}]},
    )
    first = json.loads(dispatcher.execute(call))
    approval_id = first["metadata"]["approval_id"]

    dispatcher.approve_once(approval_id)
    second = json.loads(dispatcher.execute(call))
    third = json.loads(dispatcher.execute(call))

    assert second["ok"] is True
    assert second["stdout"] == "ran"
    assert third["error_code"] == "APPROVAL_REQUIRED"
    assert len(shell.calls) == 1


def test_dispatcher_approves_unknown_powershell_command_once(tmp_path: Path):
    class FakePowerShell:
        def __init__(self):
            self.calls = []
            self.approved = []

        def approve_pipeline_once(self, pipeline):
            self.approved.append(pipeline)

        def run(self, pipeline):
            self.calls.append(pipeline)
            from tjproxy_agent.powershell import ShellResult

            return ShellResult(0, "compiled", "")

    workspace = Workspace(tmp_path, read_limit=1000, write_limit=1000)
    shell = FakePowerShell()
    policy = PolicyEngine(load_policy_config(tmp_path / "missing.toml"))
    approvals = ApprovalStore()
    dispatcher = ToolDispatcher(
        workspace,
        powershell=shell,
        output_chars=1000,
        policy_engine=policy,
        approval_store=approvals,
    )
    context = PolicyContext(profile="dev", task_id="task-1", workspace=tmp_path)
    dispatcher.set_policy_context(context)
    call = ToolCall(
        "powershell",
        {"pipeline": [{"command": "gcc", "args": ["grade_stats.c"]}]},
    )
    first = json.loads(dispatcher.execute(call))
    approval_id = first["metadata"]["approval_id"]

    dispatcher.approve_once(approval_id)
    second = json.loads(dispatcher.execute(call))

    assert first["error_code"] == "APPROVAL_REQUIRED"
    assert second["ok"] is True
    assert second["stdout"] == "compiled"
    assert shell.approved == [call.arguments["pipeline"]]
    assert shell.calls == [call.arguments["pipeline"]]


# ── Task 5: per-task policy context in AgentRunner ─────────────────────────


def test_runner_sets_and_clears_policy_context_per_task(tmp_path: Path):
    class ContextTools(FakeTools):
        def __init__(self, workspace):
            super().__init__()
            self.workspace = workspace
            self.contexts = []
            self.clears = 0

        def set_policy_context(self, context):
            self.contexts.append(context)

        def clear_approvals(self):
            self.clears += 1

    client = FakeClient(['{"type":"final","content":"done"}'])
    tools = ContextTools(Workspace(tmp_path, read_limit=1000, write_limit=1000))
    runner = AgentRunner(client, tools, max_rounds=2, system_prompt="protocol")

    runner.run("task")

    assert len(tools.contexts) == 1
    assert tools.contexts[0].task_id
    assert tools.contexts[0].profile == "dev"
    assert tools.clears == 1
