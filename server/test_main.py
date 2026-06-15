"""Tests for TJproxy Python server — executable interface spec.

These tests double as the canonical contract for main.py.
They can run end-to-end when the server fixture is available (main.py exists),
and serve as documentation otherwise.

Protocol (from design spec):
    HTTP POST /chat   →  SSE text/event-stream
    WS  ↔ Extension   →  {"type":"chat|token|done|error", ...}

Error HTTP status codes:
    503 — no WS connection / no AppID
    400 — invalid JSON body
    502 — WS disconnected mid-request
"""

import json
import asyncio
import socket
from http import HTTPStatus

import pytest
import requests
import websockets
from websockets.asyncio.client import connect

from conftest import (
    BRIDGE_TOKEN,
    EXTENSION_ORIGIN,
    SERVER_HOST,
    SERVER_PORT,
    SERVER_URL,
    WS_BASE_URL,
    WS_URL,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def ws_client():
    """Connect a single WebSocket client and yield it."""
    async with connect(WS_URL, origin=EXTENSION_ORIGIN) as ws:
        yield ws


def _raw_http(request: bytes) -> bytes:
    with socket.create_connection((SERVER_HOST, SERVER_PORT), timeout=5) as sock:
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)


# ---------------------------------------------------------------------------
# 1. WebSocket chat roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_chat_roundtrip(ws_client):
    """Simulate a full WS chat exchange end-to-end.

    Protocol direction:
        HTTP POST /chat → server sends ``{"type":"chat","message":"..."}`` over WS
        Extension replies with ``{"type":"token",...}`` then ``{"type":"done",...}``
        Server relays those to the HTTP client as SSE.

    This test drives both sides:
    1. POST /chat in a background thread
    2. WS client receives the forwarded ``chat`` message
    3. WS client replies with token + done
    4. HTTP response completes with SSE events
    """
    ws = ws_client

    async def ws_side():
        """Wait for the server to send us a chat message, then reply."""
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                assert msg["message"] == "test msg"
                await ws.send(json.dumps({"type": "token", "content": "hello"}))
                await ws.send(json.dumps({"type": "done", "tokens": 5}))
                return

    ws_task = asyncio.create_task(ws_side())

    def post_chat():
        return requests.post(
            f"{SERVER_URL}/chat",
            json={"message": "test msg"},
            stream=True,
            timeout=5,
        )

    resp = await asyncio.to_thread(post_chat)

    assert resp.status_code == HTTPStatus.OK

    lines = list(resp.iter_lines(decode_unicode=True))
    resp.close()

    await ws_task

    assert any("hello" in line for line in lines), f"SSE lines: {lines}"
    assert any("[DONE]" in line for line in lines), f"SSE lines: {lines}"


# ---------------------------------------------------------------------------
# 2. HTTP POST /chat → SSE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_chat_sse(ws_client):
    """POST /chat while a WS client is connected → receive SSE tokens.

    1. Connect WS.
    2. POST /chat {"message":"hi"} — read SSE in a background task.
    3. WS client receives the forwarded chat message, replies with tokens.
    4. Assert the SSE stream looks correct.
    """
    ws = ws_client

    async def handle_ws():
        """WS side: echo the chat, then send tokens + done."""
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                assert msg["message"] == "hi"
                await ws.send(json.dumps({"type": "token", "content": "你"}))
                await ws.send(json.dumps({"type": "token", "content": "好"}))
                await ws.send(json.dumps({"type": "done", "tokens": 2}))
                return

    # Start WS handler in the background
    ws_task = asyncio.create_task(handle_ws())

    # Make the HTTP POST (blocking call, run in thread)
    # We use requests in a thread because the event loop is running.
    def post_chat():
        return requests.post(
            f"{SERVER_URL}/chat",
            json={"message": "hi"},
            stream=True,
            timeout=5,
        )

    resp = await asyncio.to_thread(post_chat)

    assert resp.status_code == HTTPStatus.OK
    assert "text/event-stream" in resp.headers.get("content-type", "")

    lines = []
    for line in resp.iter_lines(decode_unicode=True):
        if line:
            lines.append(line)
        if len(lines) >= 4:  # enough to capture the key events
            break
    resp.close()

    await ws_task

    assert any("你" in line for line in lines), f"SSE lines: {lines}"
    assert any("好" in line for line in lines), f"SSE lines: {lines}"
    assert any("[DONE]" in line for line in lines), f"SSE lines: {lines}"


@pytest.mark.asyncio
async def test_chat_requests_are_serialized(ws_client):
    """A second HTTP request waits until the first browser chat completes."""
    ws = ws_client

    async def post(message):
        return await asyncio.to_thread(
            requests.post,
            f"{SERVER_URL}/chat",
            json={"message": message},
            timeout=5,
        )

    first_http = asyncio.create_task(post("first"))
    first_chat = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
    assert first_chat["message"] == "first"

    second_http = asyncio.create_task(post("second"))
    try:
        premature = await asyncio.wait_for(ws.recv(), timeout=0.25)
    except asyncio.TimeoutError:
        premature = None

    await ws.send(json.dumps({"type": "done"}))

    if premature is None:
        second_chat = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
    else:
        second_chat = json.loads(premature)
    await ws.send(json.dumps({"type": "done"}))

    first_resp, second_resp = await asyncio.gather(first_http, second_http)
    assert premature is None, "second request reached the extension before the first completed"
    assert second_chat["message"] == "second"
    assert first_resp.status_code == HTTPStatus.OK
    assert second_resp.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_multiline_token_is_valid_sse(ws_client):
    ws = ws_client

    async def handle_ws():
        await ws.recv()
        await ws.send(json.dumps({"type": "token", "content": "line 1\nline 2"}))
        await ws.send(json.dumps({"type": "done"}))

    ws_task = asyncio.create_task(handle_ws())
    resp = await asyncio.to_thread(
        requests.post,
        f"{SERVER_URL}/chat",
        json={"message": "multiline"},
        timeout=5,
    )
    await ws_task

    assert resp.status_code == HTTPStatus.OK
    assert resp.text.splitlines()[:3] == ["data: line 1", "data: line 2", ""]


def test_http_header_without_space_after_colon_is_parsed():
    body = b'{"message":"hello"}'
    response = _raw_http(
        b"POST /chat HTTP/1.1\r\n"
        b"Host:localhost\r\n"
        b"Content-Type:application/json\r\n"
        + f"Content-Length:{len(body)}\r\n".encode()
        + b"Connection:close\r\n\r\n"
        + body
    )

    assert response.startswith(b"HTTP/1.1 503"), response


@pytest.mark.asyncio
async def test_websocket_requires_bridge_path():
    with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
        async with connect(
            f"{WS_BASE_URL}/?token={BRIDGE_TOKEN}", origin=EXTENSION_ORIGIN
        ):
            pass
    assert exc.value.response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_websocket_requires_extension_origin():
    with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
        async with connect(WS_URL, origin="http://example.com"):
            pass
    assert exc.value.response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_websocket_rejects_token_change():
    async with connect(WS_URL, origin=EXTENSION_ORIGIN):
        pass

    with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
        async with connect(
            f"{WS_BASE_URL}/bridge?token=wrong-token", origin=EXTENSION_ORIGIN
        ):
            pass
    assert exc.value.response.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# 3. No WS connection → 503
# ---------------------------------------------------------------------------


def test_no_ws_connection_503():
    """POST /chat with no Extension connected → 503 + error SSE message."""
    resp = requests.post(
        f"{SERVER_URL}/chat",
        json={"message": "hello"},
        timeout=5,
    )

    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE

    body_lines = list(resp.iter_lines(decode_unicode=True))
    error_line = next((l for l in body_lines if l), "")
    assert "[ERROR]" in error_line, f"Expected [ERROR] in response: {body_lines}"


# ---------------------------------------------------------------------------
# 4. WS error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_error_propagation(ws_client):
    """WS sends ``{"type":"error"}`` → HTTP /chat returns error SSE."""
    ws = ws_client

    async def handle_ws():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                # Simulate an upstream error from the Extension
                await ws.send(
                    json.dumps({"type": "error", "message": "未登录"})
                )
                return

    ws_task = asyncio.create_task(handle_ws())

    def post_chat():
        return requests.post(
            f"{SERVER_URL}/chat",
            json={"message": "test"},
            timeout=5,
        )

    resp = await asyncio.to_thread(post_chat)

    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE

    error_found = False
    for line in resp.iter_lines(decode_unicode=True):
        if line and "[ERROR]" in line and "未登录" in line:
            error_found = True
            break
    resp.close()

    await ws_task
    assert error_found, "Expected [ERROR] with '未登录' in SSE response"


# ---------------------------------------------------------------------------
# 5. Invalid JSON → 400
# ---------------------------------------------------------------------------


def test_invalid_json_400():
    """Sending a malformed JSON body → 400 Bad Request."""
    resp = requests.post(
        f"{SERVER_URL}/chat",
        data="this is not json",
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_missing_message_field_400():
    """Sending valid JSON but without the required 'message' field → 400."""
    resp = requests.post(
        f"{SERVER_URL}/chat",
        json={"not_message": 123},
        timeout=5,
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# 6. WS disconnect mid-request → 502
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_disconnect_mid_request(ws_client):
    """WS client disconnects while a /chat request is in-flight → HTTP 502."""
    ws = ws_client

    async def handle_ws():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                # Disconnect abruptly — do not send token or done
                await ws.close()
                return

    ws_task = asyncio.create_task(handle_ws())

    def post_chat():
        return requests.post(
            f"{SERVER_URL}/chat",
            json={"message": "will be interrupted"},
            timeout=5,
        )

    resp = await asyncio.to_thread(post_chat)

    assert resp.status_code == HTTPStatus.BAD_GATEWAY

    body_lines = list(resp.iter_lines(decode_unicode=True))
    error_line = next((l for l in body_lines if l), "")

    assert "[ERROR]" in error_line, f"Expected [ERROR] for disconnect: {body_lines}"

    resp.close()

    # ws_task is already done (we closed the connection)
    try:
        await ws_task
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 7. Content-type validation
# ---------------------------------------------------------------------------


def test_wrong_content_type_415():
    """Non-JSON Content-Type → 415 Unsupported Media Type."""
    resp = requests.post(
        f"{SERVER_URL}/chat",
        data="message=hello",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=5,
    )
    # Accept either 415 or 400 — spec doesn't mandate which, but the server
    # should reject non-JSON bodies.
    assert resp.status_code in (HTTPStatus.UNSUPPORTED_MEDIA_TYPE, HTTPStatus.BAD_REQUEST)


# ---------------------------------------------------------------------------
# 8. OpenAI /v1/chat/completions — non-streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_non_streaming(ws_client):
    """POST /v1/chat/completions (stream=false) → single OpenAI JSON response."""
    ws = ws_client

    async def handle_ws():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                # The prompt is the messages[n].content joined by newlines
                assert msg["message"] == "你好"
                await ws.send(json.dumps({"type": "token", "content": "你好世界"}))
                await ws.send(json.dumps({"type": "done", "tokens": 4}))
                return

    ws_task = asyncio.create_task(handle_ws())

    def post():
        return requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
            },
            timeout=5,
        )

    resp = await asyncio.to_thread(post)
    await ws_task

    assert resp.status_code == HTTPStatus.OK
    assert "application/json" in resp.headers.get("content-type", "")

    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "tongji-agent"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "你好世界"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)


@pytest.mark.asyncio
async def test_openai_logging_does_not_break_on_non_gbk_text(ws_client):
    """Proxy logging must not terminate requests containing arbitrary Unicode."""
    ws = ws_client

    async def handle_ws():
        chat = json.loads(await ws.recv())
        assert "ã" in chat["message"]
        await ws.send(json.dumps({
            "type": "token",
            "request_id": chat.get("request_id"),
            "content": "reply ã",
        }))
        await ws.send(json.dumps({
            "type": "done",
            "request_id": chat.get("request_id"),
        }))

    ws_task = asyncio.create_task(handle_ws())

    resp = await asyncio.to_thread(
        requests.post,
        f"{SERVER_URL}/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "prompt ã"}],
            "stream": False,
        },
        timeout=5,
    )
    await ws_task

    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["choices"][0]["message"]["content"] == "reply ã"


# ---------------------------------------------------------------------------
# 9. OpenAI /v1/chat/completions — streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_streaming(ws_client):
    """POST /v1/chat/completions (stream=true) → SSE OpenAI chunks."""
    ws = ws_client

    async def handle_ws():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                await ws.send(json.dumps({"type": "token", "content": "你"}))
                await ws.send(json.dumps({"type": "token", "content": "好"}))
                await ws.send(json.dumps({"type": "done", "tokens": 2}))
                return

    ws_task = asyncio.create_task(handle_ws())

    def post():
        return requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
            stream=True,
            timeout=5,
        )

    resp = await asyncio.to_thread(post)
    assert resp.status_code == HTTPStatus.OK

    lines = []
    for line in resp.iter_lines(decode_unicode=True):
        if line:
            lines.append(line)
        if len(lines) >= 5:
            break
    resp.close()

    await ws_task

    # Parse the data: lines
    data_lines = [l for l in lines if l.startswith("data: ")]

    # First chunks should be tokens
    chunks = []
    for dl in data_lines:
        payload = dl[len("data: "):]
        if payload == "[DONE]":
            chunks.append("[DONE]")
        else:
            chunks.append(json.loads(payload))

    # Verify at least one token chunk and [DONE]
    token_contents = []
    for c in chunks:
        if c == "[DONE]":
            continue
        if c["object"] == "chat.completion.chunk":
            delta = c["choices"][0].get("delta", {})
            if "content" in delta:
                token_contents.append(delta["content"])

    assert "你" in token_contents, f"token_contents={token_contents}, lines={lines}"
    assert "好" in token_contents, f"token_contents={token_contents}, lines={lines}"

    # Last non-[DONE] chunk should have finish_reason
    finish_chunks = [c for c in chunks if c != "[DONE]" and
                     c["choices"][0].get("finish_reason")]
    assert len(finish_chunks) >= 1
    assert finish_chunks[-1]["choices"][0]["finish_reason"] == "stop"

    assert "[DONE]" in chunks


@pytest.mark.asyncio
async def test_openai_stream_uses_request_id_and_ignores_stale_messages(ws_client):
    """Late tokens from a previous browser request must not enter this stream."""
    ws = ws_client

    def post():
        return requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "slow"}],
                "stream": True,
            },
            stream=True,
            timeout=5,
        )

    http_task = asyncio.create_task(asyncio.to_thread(post))
    chat = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
    actual_request_id = chat.get("request_id")
    has_request_id = isinstance(actual_request_id, str) and bool(actual_request_id)
    request_id = actual_request_id or "missing-request-id"

    await ws.send(json.dumps({"type": "started", "request_id": request_id}))
    resp = await asyncio.wait_for(http_task, timeout=2)

    await ws.send(json.dumps({
        "type": "token",
        "request_id": "stale-request",
        "content": "WRONG",
    }))
    await ws.send(json.dumps({
        "type": "token",
        "request_id": request_id,
        "content": "RIGHT",
    }))
    await ws.send(json.dumps({"type": "done", "request_id": request_id}))

    lines = await asyncio.to_thread(
        lambda: list(resp.iter_lines(decode_unicode=True)))
    resp.close()
    payload = "\n".join(lines)
    assert has_request_id
    assert "RIGHT" in payload
    assert "WRONG" not in payload


@pytest.mark.asyncio
async def test_openai_stream_sends_heartbeat_while_model_is_thinking(ws_client):
    """A started stream stays visibly alive before the first answer token."""
    ws = ws_client
    body = json.dumps({
        "messages": [{"role": "user", "content": "think"}],
        "stream": True,
    }).encode()
    reader, writer = await asyncio.open_connection(SERVER_HOST, SERVER_PORT)
    writer.write(
        b"POST /v1/chat/completions HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )
    await writer.drain()

    chat = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
    actual_request_id = chat.get("request_id")
    has_request_id = isinstance(actual_request_id, str) and bool(actual_request_id)
    request_id = actual_request_id or "missing-request-id"
    await ws.send(json.dumps({"type": "started", "request_id": request_id}))

    try:
        headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
        assert b"200 OK" in headers
        heartbeat = await asyncio.wait_for(reader.readline(), timeout=0.5)
        assert has_request_id
        assert heartbeat == b": keep-alive\n"
    finally:
        await ws.send(json.dumps({"type": "done", "request_id": request_id}))
        writer.close()
        await writer.wait_closed()


# ---------------------------------------------------------------------------
# 10. OpenAI — messages concatenation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_messages_concat(ws_client):
    """Multiple messages → contents joined by newline."""
    ws = ws_client

    async def handle_ws():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                # Should be "Hello\nWorld" (joined by newline)
                assert msg["message"] == "Hello\nWorld"
                await ws.send(json.dumps({"type": "done", "tokens": 0}))
                return

    ws_task = asyncio.create_task(handle_ws())

    def post():
        return requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": "Hello"},
                    {"role": "user", "content": "World"},
                ],
            },
            timeout=5,
        )

    resp = await asyncio.to_thread(post)
    await ws_task

    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == ""


@pytest.mark.asyncio
async def test_openai_extracts_text_from_content_blocks(ws_client):
    """CC Switch content blocks must retain the final user instruction."""
    ws = ws_client

    async def handle_ws():
        chat = json.loads(await ws.recv())
        assert chat["message"] == "System context\n只回复 pong"
        await ws.send(json.dumps({
            "type": "token",
            "request_id": chat.get("request_id"),
            "content": "pong",
        }))
        await ws.send(json.dumps({
            "type": "done",
            "request_id": chat.get("request_id"),
        }))

    ws_task = asyncio.create_task(handle_ws())

    resp = await asyncio.to_thread(
        requests.post,
        f"{SERVER_URL}/v1/chat/completions",
        json={
            "messages": [
                {"role": "system", "content": "System context"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "只回复 pong"},
                        {"type": "tool_result", "content": "ignored"},
                    ],
                },
            ],
            "stream": False,
        },
        timeout=5,
    )
    await ws_task

    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["choices"][0]["message"]["content"] == "pong"


# ---------------------------------------------------------------------------
# 11. OpenAI — stream defaults to false
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_stream_default_false(ws_client):
    """When 'stream' is omitted, return non-streaming JSON (not SSE)."""
    ws = ws_client

    async def handle_ws():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                await ws.send(json.dumps({"type": "token", "content": "ok"}))
                await ws.send(json.dumps({"type": "done"}))
                return

    ws_task = asyncio.create_task(handle_ws())

    def post():
        return requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "x"}]},
            timeout=5,
        )

    resp = await asyncio.to_thread(post)
    await ws_task

    assert resp.status_code == HTTPStatus.OK
    # Should be JSON, not SSE
    assert "application/json" in resp.headers.get("content-type", "")
    body = resp.json()
    assert body["object"] == "chat.completion"


# ---------------------------------------------------------------------------
# 12. OpenAI — error: missing messages
# ---------------------------------------------------------------------------


def test_openai_missing_messages_400():
    """Missing or invalid 'messages' field → 400 OpenAI error."""
    resp = requests.post(
        f"{SERVER_URL}/v1/chat/completions",
        json={"model": "gpt-4"},
        timeout=5,
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "400"


# ---------------------------------------------------------------------------
# 13. OpenAI — error: invalid JSON
# ---------------------------------------------------------------------------


def test_openai_invalid_json_400():
    """Malformed JSON body → 400 OpenAI error."""
    resp = requests.post(
        f"{SERVER_URL}/v1/chat/completions",
        data="not json",
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    body = resp.json()
    assert "error" in body


# ---------------------------------------------------------------------------
# 14. OpenAI — error: no WS connection
# ---------------------------------------------------------------------------


def test_openai_no_ws_503():
    """POST /v1/chat/completions without Extension connected → 503."""
    resp = requests.post(
        f"{SERVER_URL}/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
        timeout=5,
    )
    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "503"


# ---------------------------------------------------------------------------
# 15. OpenAI — error: WS error propagation (non-streaming)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_ws_error_non_streaming(ws_client):
    """WS sends error → /v1/chat/completions returns 503 OpenAI error."""
    ws = ws_client

    async def handle_ws():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                await ws.send(json.dumps(
                    {"type": "error", "message": "未登录"}))
                return

    ws_task = asyncio.create_task(handle_ws())

    def post():
        return requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "test"}]},
            timeout=5,
        )

    resp = await asyncio.to_thread(post)
    await ws_task

    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    body = resp.json()
    assert "error" in body
    assert "未登录" in body["error"]["message"]


# ---------------------------------------------------------------------------
# 16. OpenAI — error: WS disconnect mid-request (non-streaming)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_ws_disconnect_non_streaming(ws_client):
    """WS disconnects mid-request → /v1/chat/completions returns 502."""
    ws = ws_client

    async def handle_ws():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                await ws.close()
                return

    ws_task = asyncio.create_task(handle_ws())

    def post():
        return requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "x"}]},
            timeout=5,
        )

    resp = await asyncio.to_thread(post)
    try:
        await ws_task
    except Exception:
        pass

    assert resp.status_code == HTTPStatus.BAD_GATEWAY
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "502"


# ---------------------------------------------------------------------------
# 17. OpenAI — empty messages content → 400
# ---------------------------------------------------------------------------


def test_openai_empty_content_400():
    """All messages have empty content → 400."""
    resp = requests.post(
        f"{SERVER_URL}/v1/chat/completions",
        json={"messages": [
            {"role": "system", "content": ""},
            {"role": "user", "content": ""},
        ]},
        timeout=5,
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    body = resp.json()
    assert "error" in body


# ---------------------------------------------------------------------------
# 18. OpenAI — wrong content type
# ---------------------------------------------------------------------------


def test_openai_wrong_content_type_415():
    """Non-JSON Content-Type → 415 OpenAI error."""
    resp = requests.post(
        f"{SERVER_URL}/v1/chat/completions",
        data="messages=hello",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=5,
    )
    assert resp.status_code in (HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                                HTTPStatus.BAD_REQUEST)


# ---------------------------------------------------------------------------
# 19. OpenAI — verify both endpoints coexist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_endpoints_work(ws_client):
    """Verify /chat (SSE) and /v1/chat/completions (OpenAI) work in same session."""
    ws = ws_client

    async def handle_ws():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chat":
                if msg["message"] == "legacy":
                    await ws.send(json.dumps({"type": "token", "content": "SSE"}))
                    await ws.send(json.dumps({"type": "done"}))
                elif msg["message"] == "openai":
                    await ws.send(json.dumps({"type": "token", "content": "JSON"}))
                    await ws.send(json.dumps({"type": "done"}))
                return  # one request per ws handler invocation

    # Test /chat first
    ws_task1 = asyncio.create_task(handle_ws())

    def post_legacy():
        return requests.post(
            f"{SERVER_URL}/chat",
            json={"message": "legacy"},
            stream=True,
            timeout=5,
        )

    resp1 = await asyncio.to_thread(post_legacy)
    lines1 = list(resp1.iter_lines(decode_unicode=True))
    resp1.close()
    await ws_task1

    assert any("SSE" in l for l in lines1)

    # Test /v1/chat/completions second (need a new ws handler)
    ws_task2 = asyncio.create_task(handle_ws())

    def post_openai():
        return requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "openai"}]},
            timeout=5,
        )

    resp2 = await asyncio.to_thread(post_openai)
    await ws_task2

    assert resp2.status_code == HTTPStatus.OK
    body = resp2.json()
    assert body["choices"][0]["message"]["content"] == "JSON"
