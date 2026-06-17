from pathlib import Path
import json
import threading
from urllib.request import Request, urlopen

from agent_tests.conftest import fake_tjproxy
from agent_tests.test_end_to_end import build_test_runner
from tjproxy_agent.debug_server import DebugAgentServer, TracingClient


def post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode())


def test_debug_server_runs_agent_through_tjproxy_client(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    responses = [
        '{"type":"tool_call","tool":"read","arguments":{"path":"README.md"}}',
        '{"type":"final","content":"read hello"}',
    ]

    with fake_tjproxy(responses) as (base_url, upstream):
        runner = build_test_runner(tmp_path, base_url)
        trace = []
        runner.client = TracingClient(runner.client, trace)
        server = DebugAgentServer(runner, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = post_json(
                f"http://127.0.0.1:{server.server_port}/run",
                {"task": "read the readme"},
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    assert result["status"] == "completed"
    assert result["content"] == "read hello"
    assert len(upstream.requests) == 2
    event_kinds = [event["kind"] for event in result["events"]]
    assert event_kinds == [
        "llm_request",
        "llm_response",
        "tool_call",
        "tool_result",
        "llm_request",
        "llm_response",
    ]
    assert result["events"][0]["message_count"] == 2
    assert len(result["events"][0]["messages"]) == 2
    assert len(result["events"][4]["messages"]) == 4
    assert result["events"][1]["content"].startswith('{"type":"tool_call"')
    assert result["events"][2]["tool"] == "read"


def test_debug_server_rejects_invalid_requests(tmp_path: Path):
    runner = build_test_runner(tmp_path, "http://127.0.0.1:9")
    server = DebugAgentServer(runner, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/run",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=5)
        except Exception as exc:
            assert "HTTP Error 400" in str(exc)
        else:
            raise AssertionError("invalid request unexpectedly succeeded")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_debug_server_can_approve_pending_tool_call(tmp_path: Path):
    from tjproxy_agent.protocol import ToolCall
    from tjproxy_agent.runner import RunOutcome

    class ApprovalRunner:
        def __init__(self):
            self.audit = lambda event: None
            self.client = object()
            self.pending_approval = ("approval-1", ToolCall("powershell", {"pipeline": []}))
            self.approved = []

        def run(self, task):
            return RunOutcome("approval_required", '{"metadata":{"approval_id":"approval-1"}}', 1)

        def approve_pending(self, approval_id):
            self.approved.append(approval_id)
            return RunOutcome("completed", "approved", 1)

    runner = ApprovalRunner()
    server = DebugAgentServer(runner, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        first = post_json(f"http://127.0.0.1:{server.server_port}/run", {"task": "x"})
        second = post_json(
            f"http://127.0.0.1:{server.server_port}/approve",
            {"approval_id": first["approval_id"]},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert runner.approved == ["approval-1"]
    assert second["status"] == "completed"
    assert second["content"] == "approved"


def test_debug_server_can_reset_runner_context(tmp_path: Path):
    class ResetRunner:
        def __init__(self):
            self.audit = lambda event: None
            self.client = object()
            self.cleared = 0

        def clear_history(self):
            self.cleared += 1

    runner = ResetRunner()
    server = DebugAgentServer(runner, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = post_json(f"http://127.0.0.1:{server.server_port}/reset", {})
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert result == {"ok": True}
    assert runner.cleared == 1
