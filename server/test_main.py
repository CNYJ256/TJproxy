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
from http import HTTPStatus

import pytest
import requests
import websockets
from websockets.asyncio.client import connect

from conftest import SERVER_URL, WS_URL, SERVER_HOST, SERVER_PORT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def ws_client():
    """Connect a single WebSocket client and yield it."""
    async with connect(WS_URL) as ws:
        yield ws


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
