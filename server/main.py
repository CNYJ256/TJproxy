"""TJproxy server -- HTTP SSE + WebSocket bridge on port 8765.

Single-file server:
- Accepts one WebSocket connection from the Chrome Extension
- Accepts HTTP POST /chat, relays to Extension via WS, streams SSE back
- Also serves GET / for the readiness check in conftest.py
"""

import asyncio
import base64
import hashlib
import json
import os
import struct
import sys
import time
from http import HTTPStatus

HOST = "localhost"
PORT = 8765
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ---------------------------------------------------------------------------
# Shared state between the WebSocket handler and HTTP handler
# ---------------------------------------------------------------------------
# Because we process one chat request at a time and have at most one
# Extension connected, we can use simple global state protected by the
# asyncio event loop (no true threading concerns).

_ws_writer = None         # asyncio.StreamWriter for the connected WS client
_pending_queue = None     # asyncio.Queue for the current in-flight HTTP request


# ---------------------------------------------------------------------------
# Low-level I/O helpers
# ---------------------------------------------------------------------------


async def _read_exactly(reader: asyncio.StreamReader, n: int) -> bytes:
    """Read exactly *n* bytes from *reader*, raising on early EOF."""
    data = b""
    while len(data) < n:
        chunk = await reader.read(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data += chunk
    return data


# ---------------------------------------------------------------------------
# HTTP response helpers
# ---------------------------------------------------------------------------


def _http_response(status: int, reason: str, body: bytes = b"",
                   extra_headers: dict | None = None) -> bytes:
    """Build a complete HTTP/1.1 response (connection: close)."""
    lines = [f"HTTP/1.1 {status} {reason}"]
    hdrs = {"Connection": "close"}
    if body:
        hdrs["Content-Length"] = str(len(body))
    if extra_headers:
        hdrs.update(extra_headers)
    for k, v in hdrs.items():
        lines.append(f"{k}: {v}")
    lines.append("")
    lines.append("")
    return ("\r\n".join(lines)).encode() + body


async def _send_http(writer: asyncio.StreamWriter, status: int, reason: str,
                     body: bytes = b"", extra_headers: dict | None = None) -> None:
    """Write a complete HTTP response and drain."""
    writer.write(_http_response(status, reason, body, extra_headers))
    await writer.drain()


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse_line(text: str) -> bytes:
    """Format one SSE data event."""
    return f"data: {text}\n\n".encode()


async def _send_sse_header(writer: asyncio.StreamWriter, status: int,
                           reason: str) -> None:
    """Write HTTP status line + SSE headers, then drain."""
    header = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: text/event-stream; charset=utf-8\r\n"
        "Cache-Control: no-cache\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    writer.write(header.encode())
    await writer.drain()


async def _sse_write(writer: asyncio.StreamWriter, text: str) -> None:
    """Write a single SSE data event and drain."""
    writer.write(_sse_line(text))
    await writer.drain()


# ---------------------------------------------------------------------------
# OpenAI API helpers
# ---------------------------------------------------------------------------


def _make_openai_id() -> str:
    """Generate a chatcmpl-prefixed random ID with timestamp."""
    rnd = os.urandom(16).hex()
    ts = int(time.time())
    return f"chatcmpl-{rnd}-{ts}"


def _make_openai_chunk(chat_id: str, created: int, content: str | None,
                       finish_reason: str | None = None) -> str:
    """Build one OpenAI streaming chunk JSON line."""
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    choice = {"index": 0, "delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return json.dumps({
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": "tongji-agent",
        "choices": [choice],
    }, ensure_ascii=False)


def _make_openai_response(chat_id: str, created: int, content: str) -> str:
    """Build a full (non-streaming) OpenAI chat completion JSON body."""
    return json.dumps({
        "id": chat_id,
        "object": "chat.completion",
        "created": created,
        "model": "tongji-agent",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
    }, ensure_ascii=False)


def _make_openai_error_json(status: int, message: str) -> str:
    """Build an OpenAI-style error JSON body."""
    return json.dumps({
        "error": {
            "message": message,
            "type": "server_error",
            "code": str(status),
        }
    }, ensure_ascii=False)


async def _send_openai_error(writer: asyncio.StreamWriter, status: int,
                             message: str) -> None:
    """Write an OpenAI error response with the appropriate HTTP status."""
    body = _make_openai_error_json(status, message)
    await _send_http(writer, status, "Error", body=body.encode(),
                     extra_headers={"Content-Type": "application/json"})


# ---------------------------------------------------------------------------
# WebSocket frame helpers
# ---------------------------------------------------------------------------


def _make_ws_frame(opcode: int, payload: bytes) -> bytes:
    """Build an unmasked WebSocket frame (server -> client)."""
    frame = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        frame += bytes([length])
    elif length < 65536:
        frame += bytes([126]) + struct.pack("!H", length)
    else:
        frame += bytes([127]) + struct.pack("!Q", length)
    return frame + payload


async def _send_ws(writer: asyncio.StreamWriter, opcode: int,
                   payload: bytes) -> None:
    """Send one WebSocket frame and drain."""
    writer.write(_make_ws_frame(opcode, payload))
    await writer.drain()


async def _recv_ws_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read one WebSocket frame.  Returns (opcode, payload)."""
    header = await _read_exactly(reader, 2)
    byte1, byte2 = header[0], header[1]
    opcode = byte1 & 0x0F
    masked = (byte2 & 0x80) != 0
    plen = byte2 & 0x7F

    if plen == 126:
        ext = await _read_exactly(reader, 2)
        plen = struct.unpack("!H", ext)[0]
    elif plen == 127:
        ext = await _read_exactly(reader, 8)
        plen = struct.unpack("!Q", ext)[0]

    mask = b""
    if masked:
        mask = await _read_exactly(reader, 4)

    payload = await _read_exactly(reader, plen)
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    return opcode, payload


# ---------------------------------------------------------------------------
# HTTP POST /chat handler
# ---------------------------------------------------------------------------


async def _handle_chat(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                       headers: dict) -> None:
    """Handle POST /chat -- validate, forward to WS, stream SSE back."""
    global _pending_queue

    # Read body
    content_length = int(headers.get("content-length", 0))
    body = b""
    if content_length > 0:
        body = await _read_exactly(reader, content_length)

    # Validate Content-Type
    ct = headers.get("content-type", "")
    if "application/json" not in ct:
        await _send_http(writer, 415, "Unsupported Media Type")
        return

    # Parse JSON
    try:
        data = json.loads(body.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        await _send_http(writer, 400, "Bad Request")
        return

    if "message" not in data:
        await _send_http(writer, 400, "Bad Request")
        return

    message = data["message"]
    print(f"[TJproxy] >>> {message}", flush=True)

    # Must have a connected WS client
    if _ws_writer is None:
        await _send_sse_header(writer, 503, "Service Unavailable")
        await _sse_write(writer, "[ERROR] 无 Extension 连接，请检查插件是否已打开")
        return

    # Create queue for this request
    _pending_queue = asyncio.Queue()

    try:
        # Forward chat message to Extension
        chat = json.dumps({"type": "chat", "message": message})
        await _send_ws(_ws_writer, 0x1, chat.encode())

        # Wait for first response to determine HTTP status
        try:
            first = await asyncio.wait_for(_pending_queue.get(), timeout=30)
        except asyncio.TimeoutError:
            await _send_sse_header(writer, 502, "Bad Gateway")
            await _sse_write(writer, "[ERROR] Extension 响应超时")
            return

        typ = first.get("type")

        if typ == "error":
            await _send_sse_header(writer, 503, "Service Unavailable")
            await _sse_write(writer, f"[ERROR] {first.get('message', '')}")
            return

        if typ == "_ws_closed":
            await _send_sse_header(writer, 502, "Bad Gateway")
            await _sse_write(writer, "[ERROR] Extension 连接中途断开")
            return

        # Valid first response -- send SSE header with 200
        await _send_sse_header(writer, 200, "OK")
        response_text = ""

        if typ == "token":
            response_text += first["content"]
            await _sse_write(writer, first["content"])
        elif typ == "done":
            print(f"[TJproxy] <<< (empty)", flush=True)
            await _sse_write(writer, "[DONE]")
            return

        # Process remaining responses
        while True:
            try:
                msg = await asyncio.wait_for(_pending_queue.get(), timeout=30)
            except asyncio.TimeoutError:
                await _sse_write(writer, "[ERROR] Extension 响应超时")
                return

            typ = msg.get("type")

            if typ == "token":
                response_text += msg["content"]
                await _sse_write(writer, msg["content"])
            elif typ == "done":
                print(f"[TJproxy] <<< {response_text}", flush=True)
                await _sse_write(writer, "[DONE]")
                return
            elif typ == "error":
                await _sse_write(writer, f"[ERROR] {msg.get('message', '')}")
                return
            elif typ == "_ws_closed":
                await _sse_write(writer, "[ERROR] Extension 连接中途断开")
                return
            # Unknown type -- ignore

    finally:
        _pending_queue = None


# ---------------------------------------------------------------------------
# HTTP POST /v1/chat/completions handler (OpenAI API compatible)
# ---------------------------------------------------------------------------


async def _handle_chat_completions(reader: asyncio.StreamReader,
                                   writer: asyncio.StreamWriter,
                                   headers: dict) -> None:
    """Handle POST /v1/chat/completions -- parse OpenAI request, forward to WS,
    return OpenAI-format response (streaming or non-streaming)."""
    global _pending_queue

    # Read body
    content_length = int(headers.get("content-length", 0))
    body = b""
    if content_length > 0:
        body = await _read_exactly(reader, content_length)

    # Validate Content-Type
    ct = headers.get("content-type", "")
    if "application/json" not in ct:
        await _send_openai_error(writer, 415, "Unsupported Media Type")
        return

    # Parse JSON
    try:
        data = json.loads(body.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        await _send_openai_error(writer, 400, "Invalid JSON body")
        return

    if "messages" not in data or not isinstance(data["messages"], list):
        await _send_openai_error(writer, 400,
                                 "Missing or invalid 'messages' field")
        return

    # Build prompt by joining all message contents with newlines
    prompt_parts: list[str] = []
    for m in data["messages"]:
        if isinstance(m, dict) and "content" in m:
            c = m["content"]
            if isinstance(c, str) and c:
                prompt_parts.append(c)
    prompt = "\n".join(prompt_parts)
    print(f"[TJproxy] >>> {prompt}", flush=True)

    if not prompt:
        await _send_openai_error(writer, 400, "Empty message content")
        return

    stream = data.get("stream", False)
    chat_id = _make_openai_id()
    created = int(time.time())

    # Must have a connected WS client
    if _ws_writer is None:
        await _send_openai_error(writer, 503,
                                 "No Extension connection. Please check the plugin.")
        return

    # Create queue for this request
    _pending_queue = asyncio.Queue()

    try:
        # Forward chat message to Extension
        chat = json.dumps({"type": "chat", "message": prompt})
        await _send_ws(_ws_writer, 0x1, chat.encode())

        # Wait for first response to determine status / routing
        try:
            first = await asyncio.wait_for(_pending_queue.get(), timeout=30)
        except asyncio.TimeoutError:
            await _send_openai_error(writer, 502,
                                     "Extension response timeout")
            return

        typ = first.get("type")

        if typ == "error":
            await _send_openai_error(writer, 503,
                                     first.get("message", "Extension error"))
            return

        if typ == "_ws_closed":
            await _send_openai_error(writer, 502,
                                     "Extension connection closed mid-request")
            return

        if stream:
            # ----- streaming mode -----
            await _send_sse_header(writer, 200, "OK")
            response_text = ""

            if typ == "token":
                response_text += first["content"]
                chunk = _make_openai_chunk(chat_id, created, first["content"])
                await _sse_write(writer, chunk)
            elif typ == "done":
                print(f"[TJproxy] <<< (empty)", flush=True)
                chunk = _make_openai_chunk(chat_id, created, None,
                                           finish_reason="stop")
                await _sse_write(writer, chunk)
                await _sse_write(writer, "[DONE]")
                return

            # Process remaining WS messages
            while True:
                try:
                    msg = await asyncio.wait_for(_pending_queue.get(),
                                                 timeout=30)
                except asyncio.TimeoutError:
                    err = _make_openai_error_json(502,
                                                  "Extension response timeout")
                    await _sse_write(writer, err)
                    return

                typ = msg.get("type")

                if typ == "token":
                    response_text += msg["content"]
                    chunk = _make_openai_chunk(chat_id, created,
                                               msg["content"])
                    await _sse_write(writer, chunk)
                elif typ == "done":
                    print(f"[TJproxy] <<< {response_text}", flush=True)
                    chunk = _make_openai_chunk(chat_id, created, None,
                                               finish_reason="stop")
                    await _sse_write(writer, chunk)
                    await _sse_write(writer, "[DONE]")
                    return
                elif typ == "error":
                    err = _make_openai_error_json(
                        503, msg.get("message", "Extension error"))
                    await _sse_write(writer, err)
                    return
                elif typ == "_ws_closed":
                    err = _make_openai_error_json(
                        502, "Extension connection closed")
                    await _sse_write(writer, err)
                    return
        else:
            # ----- non-streaming mode: accumulate full content -----
            full_content = ""

            if typ == "token":
                full_content += first["content"]

            if typ != "done":
                while True:
                    try:
                        msg = await asyncio.wait_for(_pending_queue.get(),
                                                     timeout=30)
                    except asyncio.TimeoutError:
                        await _send_openai_error(writer, 502,
                                                 "Extension response timeout")
                        return

                    typ = msg.get("type")

                    if typ == "token":
                        full_content += msg["content"]
                    elif typ == "done":
                        break
                    elif typ == "error":
                        await _send_openai_error(
                            writer, 503,
                            msg.get("message", "Extension error"))
                        return
                    elif typ == "_ws_closed":
                        await _send_openai_error(
                            writer, 502,
                            "Extension connection closed")
                        return

            print(f"[TJproxy] <<< {full_content}", flush=True)

            # Build and send the complete OpenAI response
            response = _make_openai_response(chat_id, created, full_content)
            await _send_http(writer, 200, "OK", body=response.encode(),
                             extra_headers={"Content-Type": "application/json"})

    finally:
        _pending_queue = None


# ---------------------------------------------------------------------------
# WebSocket upgrade handler
# ---------------------------------------------------------------------------


async def _handle_ws(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     headers: dict) -> None:
    """Perform WebSocket upgrade, then relay Extension messages to HTTP."""
    global _ws_writer, _pending_queue

    # Handshake
    key = headers.get("sec-websocket-key", "")
    accept = base64.b64encode(
        hashlib.sha1(key.encode() + WS_GUID).digest()
    ).decode()

    handshake = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    writer.write(handshake.encode())
    await writer.drain()

    _ws_writer = writer

    try:
        while True:
            opcode, payload = await _recv_ws_frame(reader)

            if opcode == 0x8:  # close
                # Echo close frame back
                try:
                    await _send_ws(writer, 0x8, payload[:2] if len(payload) >= 2 else b"\x03\xe8")
                except Exception:
                    pass
                break

            elif opcode == 0x9:  # ping
                await _send_ws(writer, 0xA, payload)

            elif opcode == 0x1:  # text
                try:
                    msg = json.loads(payload.decode())
                except Exception:
                    continue
                q = _pending_queue
                if q is not None:
                    await q.put(msg)

            # Ignore other opcodes (binary, continuation)
    except Exception:
        pass
    finally:
        _ws_writer = None
        # Wake up any pending HTTP request so it can report disconnect
        q = _pending_queue
        if q is not None:
            try:
                q.put_nowait({"type": "_ws_closed"})
            except asyncio.QueueFull:
                pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main connection dispatch
# ---------------------------------------------------------------------------


async def handle_client(reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter) -> None:
    """Dispatch each TCP connection to HTTP or WebSocket handler."""
    is_websocket = False
    try:
        # Read request line
        request_line = await reader.readline()
        if not request_line:
            return

        parts = request_line.decode(errors="replace").strip().split()
        if len(parts) < 2:
            return
        method, path = parts[0].upper(), parts[1]

        # Read headers
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            line_str = line.decode(errors="replace").strip()
            if ": " in line_str:
                k, v = line_str.split(": ", 1)
                headers[k.lower()] = v

        # Route
        upgrade = headers.get("upgrade", "").lower()
        if upgrade == "websocket":
            is_websocket = True
            await _handle_ws(reader, writer, headers)
        elif method == "POST" and path == "/chat":
            await _handle_chat(reader, writer, headers)
        elif method == "POST" and path == "/v1/chat/completions":
            await _handle_chat_completions(reader, writer, headers)
        elif method == "GET" and path == "/v1/models":
            models_json = json.dumps({
                "object": "list",
                "data": [{"id": "tongji-agent", "object": "model", "created": 0, "owned_by": "tongji"}]
            }).encode()
            await _send_http(writer, 200, "OK", body=models_json,
                           extra_headers={"Content-Type": "application/json"})
        elif method == "GET" and path == "/":
            await _send_http(writer, 200, "OK", body=b"OK")
        else:
            await _send_http(writer, 404, "Not Found")
    except Exception:
        pass
    finally:
        if not is_websocket:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    srv = await asyncio.start_server(handle_client, HOST, PORT)
    print(f"TJproxy server listening on http://{HOST}:{PORT}", flush=True)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
