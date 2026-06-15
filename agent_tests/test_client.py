from pathlib import Path
import socket
import time
from unittest.mock import Mock

import pytest
import requests

from agent_tests.conftest import fake_tjproxy
from tjproxy_agent.client import ServiceError, ServiceManager, TJproxyClient


def test_probe_accepts_tongji_model():
    with fake_tjproxy([]) as (base_url, _):
        assert TJproxyClient(base_url, request_timeout=2).is_compatible()


def test_complete_sends_non_streaming_messages():
    with fake_tjproxy(['{"type":"final","content":"done"}']) as (
        base_url,
        server,
    ):
        client = TJproxyClient(base_url, request_timeout=2)
        answer = client.complete([{"role": "user", "content": "task"}])

        assert answer.endswith('"done"}')
        assert server.requests[0]["stream"] is False
        assert server.requests[0]["model"] == "tongji-agent"


def test_complete_retries_one_connection_loss(monkeypatch):
    client = TJproxyClient("http://localhost:8765", request_timeout=2)
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "done"}}]
    }
    calls = iter([requests.ConnectionError("lost"), response])

    def post(*args, **kwargs):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(client, "is_compatible", lambda: True)

    assert client.complete([{"role": "user", "content": "task"}]) == "done"


def test_invalid_completion_shape_is_rejected():
    with fake_tjproxy(["unused"]) as (base_url, _):
        client = TJproxyClient(base_url, request_timeout=2)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": []}
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response)
            with pytest.raises(ServiceError, match="invalid completion"):
                client.complete([])


def test_incompatible_occupied_port_is_not_replaced(monkeypatch, tmp_path: Path):
    manager = ServiceManager(
        tmp_path / "main.py", "http://localhost:9", 0.1, request_timeout=2
    )
    monkeypatch.setattr(manager.client, "port_is_open", lambda: True)
    monkeypatch.setattr(manager.client, "is_compatible", lambda: False)

    with pytest.raises(ServiceError, match="incompatible"):
        manager.ensure_running()


def test_compatible_existing_service_is_reused_and_not_owned(tmp_path: Path):
    with fake_tjproxy([]) as (base_url, _):
        manager = ServiceManager(
            tmp_path / "missing.py", base_url, 1, request_timeout=2
        )

        assert manager.ensure_running().is_compatible()
        assert manager.owns_process is False
        manager.close()


def test_manager_starts_and_stops_owned_compatible_service(tmp_path: Path):
    port = _free_port()
    script = tmp_path / "fake_server.py"
    script.write_text(_compatible_server_script(), encoding="utf-8")
    manager = ServiceManager(
        script, f"http://localhost:{port}", 5, request_timeout=2
    )

    manager.ensure_running()
    assert manager.owns_process is True
    pid = manager.process.pid
    manager.close()

    assert manager.owns_process is False
    assert _pid_exited(pid)


def test_readiness_timeout_cleans_up_child(tmp_path: Path):
    port = _free_port()
    script = tmp_path / "sleep.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    manager = ServiceManager(
        script, f"http://localhost:{port}", 0.2, request_timeout=2
    )

    with pytest.raises(ServiceError, match="readiness timeout"):
        manager.ensure_running()

    assert manager.owns_process is False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


def _pid_exited(pid: int) -> bool:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            import os

            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.05)
    return False


def _compatible_server_script() -> str:
    return """
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/v1/models':
            self.send_error(404)
            return
        body = json.dumps({'data': [{'id': 'tongji-agent'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

HTTPServer(('localhost', int(os.environ['TJPROXY_PORT'])), Handler).serve_forever()
"""
