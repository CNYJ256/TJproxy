from pathlib import Path

from agent_tests.conftest import fake_tjproxy
from tjproxy_agent.client import TJproxyClient
from tjproxy_agent.config import PowerShellConfig
from tjproxy_agent.powershell import PowerShellExecutor
from tjproxy_agent.runner import AgentRunner, ToolDispatcher
from tjproxy_agent.workspace import Workspace


def build_test_runner(
    tmp_path: Path, base_url: str, *, max_rounds: int = 32
) -> AgentRunner:
    workspace = Workspace(tmp_path, read_limit=1000, write_limit=1000)
    shell = PowerShellExecutor(
        workspace,
        PowerShellConfig(),
        command_chars=1000,
        output_chars=1000,
    )
    return AgentRunner(
        TJproxyClient(base_url, request_timeout=2),
        ToolDispatcher(workspace, shell, output_chars=1000),
        max_rounds=max_rounds,
        system_prompt="protocol",
    )


def test_complete_multi_round_harness_without_live_tongji(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    responses = [
        '{"type":"tool_call","tool":"read","arguments":{"path":"README.md"}}',
        '{"type":"final","content":"read hello"}',
        '{"type":"final","content":"second task remembered"}',
    ]

    with fake_tjproxy(responses) as (base_url, server):
        runner = build_test_runner(tmp_path, base_url)
        first = runner.run("read the readme")
        second = runner.run("continue")
        requests = list(server.requests)

    assert first.content == "read hello"
    assert second.content == "second task remembered"
    assert "tool_result" in requests[1]["messages"][-1]["content"]
    assert any(
        message["content"] == "read the readme"
        for message in requests[2]["messages"]
    )


def test_round_limited_task_does_not_break_next_task(tmp_path: Path):
    responses = ["bad", "bad", '{"type":"final","content":"recovered"}']

    with fake_tjproxy(responses) as (base_url, _):
        runner = build_test_runner(tmp_path, base_url, max_rounds=2)
        first = runner.run("bad task")
        second = runner.run("next task")

    assert first.status == "round_limit"
    assert second.content == "recovered"


def test_write_then_read_round_trip_uses_real_workspace_tools(tmp_path: Path):
    responses = [
        '{"type":"tool_call","tool":"write","arguments":'
        '{"path":"new.txt","content":"created"}}',
        '{"type":"tool_call","tool":"read","arguments":{"path":"new.txt"}}',
        '{"type":"final","content":"verified"}',
    ]

    with fake_tjproxy(responses) as (base_url, _):
        outcome = build_test_runner(tmp_path, base_url).run("create and verify")

    assert outcome.content == "verified"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "created"
