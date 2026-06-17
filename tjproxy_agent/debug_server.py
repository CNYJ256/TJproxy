from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any

from .client import ServiceError
from .protocol import ToolCall


class TracingClient:
    def __init__(self, client: Any, events: list[dict[str, Any]]):
        self.client = client
        self.events = events

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.events.append(
            {
                "kind": "llm_request",
                "message_count": len(messages),
                "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
            }
        )
        content = self.client.complete(messages)
        self.events.append({"kind": "llm_response", "content": content})
        return content


class DebugAgentServer(ThreadingHTTPServer):
    def __init__(self, runner: Any, *, host: str, port: int):
        super().__init__((host, port), _DebugHandler)
        self.runner = runner
        self.events: list[dict[str, Any]] = []
        if not isinstance(getattr(runner, "client", None), TracingClient):
            runner.client = TracingClient(runner.client, self.events)
        else:
            self.events = runner.client.events


class _DebugHandler(BaseHTTPRequestHandler):
    server: DebugAgentServer

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self._send_json({"ok": True})

    def do_POST(self) -> None:
        if self.path == "/run":
            self._handle_run()
            return
        if self.path == "/approve":
            self._handle_approve()
            return
        if self.path == "/reset":
            self._handle_reset()
            return
        self.send_error(404)

    def _handle_run(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        task = payload.get("task")
        if not isinstance(task, str) or not task.strip():
            self._send_json({"error": "task must be a non-empty string"}, status=400)
            return

        events = self.server.events
        events.clear()

        previous_audit = self.server.runner.audit
        self.server.runner.audit = _trace_audit(events)
        try:
            outcome = self.server.runner.run(task)
        except ServiceError as exc:
            self._send_json(
                {"status": "service_error", "content": str(exc), "events": events},
                status=502,
            )
            return
        finally:
            self.server.runner.audit = previous_audit

        self._send_outcome(outcome, events)

    def _handle_approve(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        approval_id = payload.get("approval_id")
        if not isinstance(approval_id, str) or not approval_id.strip():
            self._send_json(
                {"error": "approval_id must be a non-empty string"},
                status=400,
            )
            return

        events = self.server.events
        previous_audit = self.server.runner.audit
        self.server.runner.audit = _trace_audit(events)
        try:
            outcome = self.server.runner.approve_pending(approval_id)
        finally:
            self.server.runner.audit = previous_audit

        self._send_outcome(outcome, events)

    def _handle_reset(self) -> None:
        try:
            self._read_json()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        self.server.events.clear()
        self.server.runner.clear_history()
        self._send_json({"ok": True})

    def _send_outcome(self, outcome: Any, events: list[dict[str, Any]]) -> None:
        pending = getattr(self.server.runner, "pending_approval", None)
        approval_id = pending[0] if outcome.status == "approval_required" and pending else None
        self._send_json(
            {
                "status": outcome.status,
                "content": outcome.content,
                "rounds": outcome.rounds,
                "approval_id": approval_id,
                "events": events,
            }
        )

    def _read_json(self) -> dict[str, Any]:
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ValueError("Content-Type must be application/json")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if size <= 0:
            raise ValueError("request body is required")
        try:
            payload = json.loads(self.rfile.read(size).decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return payload

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def _trace_audit(events: list[dict[str, Any]]):
    def audit(event: tuple[str, object]) -> None:
        kind, value = event
        if kind == "tool_call" and isinstance(value, ToolCall):
            events.append(
                {
                    "kind": "tool_call",
                    "tool": value.tool,
                    "arguments": value.arguments,
                }
            )
            return
        if kind == "tool_result" and isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = {"raw": value}
            events.append({"kind": "tool_result", "result": parsed})
            return
        events.append({"kind": kind, "value": str(value)})

    return audit
