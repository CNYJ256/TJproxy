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
from urllib.parse import parse_qs, urlsplit

HOST = "localhost"
PORT = int(os.environ.get("TJPROXY_PORT", "8765"))
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_PATH = "/bridge"
MAX_REQUEST_LINE = 8192
MAX_HEADER_BYTES = 32768
MAX_BODY_BYTES = 1024 * 1024
BRIDGE_IDLE_TIMEOUT = float(os.environ.get("TJPROXY_IDLE_TIMEOUT", "300"))
SSE_HEARTBEAT_INTERVAL = float(
    os.environ.get("TJPROXY_SSE_HEARTBEAT_INTERVAL", "15"))


# ---------------------------------------------------------------------------
# Shared state between the WebSocket handler and HTTP handler
# ---------------------------------------------------------------------------
# Because we process one chat request at a time and have at most one
# Extension connected, we can use simple global state protected by the
# asyncio event loop (no true threading concerns).

_ws_writer = None         # asyncio.StreamWriter for the connected WS client
_pending_queue = None     # asyncio.Queue for the current in-flight HTTP request
_pending_request_id = None
_bridge_token = None      # Trust-on-first-use token supplied by the Extension
_chat_lock = asyncio.Lock()


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
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    fields = "".join(f"data: {line}\n" for line in normalized.split("\n"))
    return f"{fields}\n".encode()


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


async def _sse_keepalive(writer: asyncio.StreamWriter) -> None:
    """Write an SSE comment so clients and proxies keep the stream open."""
    writer.write(b": keep-alive\n\n")
    await writer.drain()


async def _next_bridge_message(queue: asyncio.Queue,
                               stream_writer: asyncio.StreamWriter | None = None
                               ) -> dict:
    """Wait for bridge activity, emitting SSE heartbeats when streaming."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + BRIDGE_IDLE_TIMEOUT
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        wait_time = remaining
        if stream_writer is not None:
            wait_time = min(wait_time, SSE_HEARTBEAT_INTERVAL)
        try:
            return await asyncio.wait_for(queue.get(), timeout=wait_time)
        except asyncio.TimeoutError:
            if stream_writer is None or loop.time() >= deadline:
                raise
            await _sse_keepalive(stream_writer)


def _make_request_id() -> str:
    return os.urandom(16).hex()


async def _cancel_bridge_request(request_id: str) -> None:
    writer = _ws_writer
    if writer is None or writer.is_closing():
        return
    try:
        payload = json.dumps({"type": "cancel", "request_id": request_id})
        await _send_ws(writer, 0x1, payload.encode())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# OpenAI API helpers
# ---------------------------------------------------------------------------


def _make_openai_id() -> str:
    """Generate a chatcmpl-prefixed random ID with timestamp."""
    rnd = os.urandom(16).hex()
    ts = int(time.time())
    return f"chatcmpl-{rnd}-{ts}"


def _extract_openai_text(content) -> list[str]:
    """Extract plain text from string or OpenAI content-block messages."""
    if isinstance(content, str):
        return [content] if content else []
    if not isinstance(content, list):
        return []

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return parts


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
    global _pending_queue, _pending_request_id

    # Read body
    try:
        content_length = int(headers.get("content-length", 0))
    except ValueError:
        await _send_http(writer, 400, "Bad Request")
        return
    if content_length < 0 or content_length > MAX_BODY_BYTES:
        await _send_http(writer, 413, "Content Too Large")
        return
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

    # Validate optional files
    files = data.get("files")
    if files is not None:
        if not isinstance(files, list):
            await _send_http(writer, 400, "Bad Request")
            return
        for f in files:
            if not isinstance(f, dict) or not f.get("content"):
                await _send_http(writer, 400, "Bad Request")
                return

    message = data["message"]
    print(f"[TJproxy] >>> request ({len(str(message))} chars)", flush=True)

    # Must have a connected WS client
    if _ws_writer is None:
        await _send_sse_header(writer, 503, "Service Unavailable")
        await _sse_write(writer, "[ERROR] 无 Extension 连接，请检查插件是否已打开")
        return

    # Create queue for this request
    request_id = _make_request_id()
    _pending_queue = asyncio.Queue()
    _pending_request_id = request_id
    bridge_finished = False

    try:
        # Forward chat message to Extension
        chat = {"type": "chat", "message": message, "request_id": request_id}
        if files is not None:
            chat["files"] = files
        await _send_ws(_ws_writer, 0x1, json.dumps(chat).encode())

        # Wait for first response to determine HTTP status
        try:
            first = await _next_bridge_message(_pending_queue)
        except asyncio.TimeoutError:
            await _send_sse_header(writer, 502, "Bad Gateway")
            await _sse_write(writer, "[ERROR] Extension 响应超时")
            return

        typ = first.get("type")

        if typ == "error":
            bridge_finished = True
            await _send_sse_header(writer, 503, "Service Unavailable")
            await _sse_write(writer, f"[ERROR] {first.get('message', '')}")
            return

        if typ == "_ws_closed":
            bridge_finished = True
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
            bridge_finished = True
            print(f"[TJproxy] <<< (empty)", flush=True)
            await _sse_write(writer, "[DONE]")
            return

        # Process remaining responses
        while True:
            try:
                msg = await _next_bridge_message(_pending_queue, writer)
            except asyncio.TimeoutError:
                await _sse_write(writer, "[ERROR] Extension 响应超时")
                return

            typ = msg.get("type")

            if typ == "token":
                response_text += msg["content"]
                await _sse_write(writer, msg["content"])
            elif typ == "done":
                bridge_finished = True
                print(f"[TJproxy] <<< response ({len(response_text)} chars)",
                      flush=True)
                await _sse_write(writer, "[DONE]")
                return
            elif typ == "error":
                bridge_finished = True
                await _sse_write(writer, f"[ERROR] {msg.get('message', '')}")
                return
            elif typ == "_ws_closed":
                bridge_finished = True
                await _sse_write(writer, "[ERROR] Extension 连接中途断开")
                return
            elif typ in ("started", "activity"):
                continue
            # Unknown type -- ignore

    finally:
        if not bridge_finished:
            await _cancel_bridge_request(request_id)
        if _pending_request_id == request_id:
            _pending_queue = None
            _pending_request_id = None


# ---------------------------------------------------------------------------
# HTTP POST /v1/chat/completions handler (OpenAI API compatible)
# ---------------------------------------------------------------------------


async def _handle_chat_completions(reader: asyncio.StreamReader,
                                   writer: asyncio.StreamWriter,
                                   headers: dict) -> None:
    """Handle POST /v1/chat/completions -- parse OpenAI request, forward to WS,
    return OpenAI-format response (streaming or non-streaming)."""
    global _pending_queue, _pending_request_id

    # Read body
    try:
        content_length = int(headers.get("content-length", 0))
    except ValueError:
        await _send_openai_error(writer, 400, "Invalid Content-Length")
        return
    if content_length < 0 or content_length > MAX_BODY_BYTES:
        await _send_openai_error(writer, 413, "Request body too large")
        return
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

    # Build a plain-text prompt from string and content-block messages.
    prompt_parts: list[str] = []
    for m in data["messages"]:
        if isinstance(m, dict) and "content" in m:
            prompt_parts.extend(_extract_openai_text(m["content"]))
    prompt = "\n".join(prompt_parts)
    print(f"[TJproxy] >>> request ({len(prompt)} chars)", flush=True)

    if not prompt:
        await _send_openai_error(writer, 400, "Empty message content")
        return

    # Validate optional files
    files = data.get("files")
    if files is not None:
        if not isinstance(files, list):
            await _send_openai_error(writer, 400, "Invalid 'files' field")
            return
        for f in files:
            if not isinstance(f, dict) or not f.get("content"):
                await _send_openai_error(writer, 400,
                                         "Each file must have a non-empty 'content'")
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
    request_id = _make_request_id()
    _pending_queue = asyncio.Queue()
    _pending_request_id = request_id
    bridge_finished = False

    try:
        # Forward chat message to Extension
        chat = {"type": "chat", "message": prompt, "request_id": request_id}
        if files is not None:
            chat["files"] = files
        await _send_ws(_ws_writer, 0x1, json.dumps(chat).encode())

        # Wait for first response to determine status / routing
        try:
            first = await _next_bridge_message(_pending_queue)
        except asyncio.TimeoutError:
            await _send_openai_error(writer, 502,
                                     "Extension response timeout")
            return

        typ = first.get("type")

        if typ == "error":
            bridge_finished = True
            await _send_openai_error(writer, 503,
                                     first.get("message", "Extension error"))
            return

        if typ == "_ws_closed":
            bridge_finished = True
            await _send_openai_error(writer, 502,
                                     "Extension connection closed mid-request")
            return

        if typ == "done":
            bridge_finished = True

        if stream:
            # ----- streaming mode -----
            await _send_sse_header(writer, 200, "OK")
            response_text = ""

            if typ == "token":
                response_text += first["content"]
                chunk = _make_openai_chunk(chat_id, created, first["content"])
                await _sse_write(writer, chunk)
            elif typ == "done":
                bridge_finished = True
                print(f"[TJproxy] <<< (empty)", flush=True)
                chunk = _make_openai_chunk(chat_id, created, None,
                                           finish_reason="stop")
                await _sse_write(writer, chunk)
                await _sse_write(writer, "[DONE]")
                return

            # Process remaining WS messages
            while True:
                try:
                    msg = await _next_bridge_message(_pending_queue, writer)
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
                    bridge_finished = True
                    print(
                        f"[TJproxy] <<< response ({len(response_text)} chars)",
                        flush=True)
                    chunk = _make_openai_chunk(chat_id, created, None,
                                               finish_reason="stop")
                    await _sse_write(writer, chunk)
                    await _sse_write(writer, "[DONE]")
                    return
                elif typ == "error":
                    bridge_finished = True
                    err = _make_openai_error_json(
                        503, msg.get("message", "Extension error"))
                    await _sse_write(writer, err)
                    return
                elif typ == "_ws_closed":
                    bridge_finished = True
                    err = _make_openai_error_json(
                        502, "Extension connection closed")
                    await _sse_write(writer, err)
                    return
                elif typ in ("started", "activity"):
                    continue
        else:
            # ----- non-streaming mode: accumulate full content -----
            full_content = ""

            if typ == "token":
                full_content += first["content"]

            if typ != "done":
                while True:
                    try:
                        msg = await _next_bridge_message(_pending_queue)
                    except asyncio.TimeoutError:
                        await _send_openai_error(writer, 502,
                                                 "Extension response timeout")
                        return

                    typ = msg.get("type")

                    if typ == "token":
                        full_content += msg["content"]
                    elif typ == "done":
                        bridge_finished = True
                        break
                    elif typ == "error":
                        bridge_finished = True
                        await _send_openai_error(
                            writer, 503,
                            msg.get("message", "Extension error"))
                        return
                    elif typ == "_ws_closed":
                        bridge_finished = True
                        await _send_openai_error(
                            writer, 502,
                            "Extension connection closed")
                        return
                    elif typ in ("started", "activity"):
                        continue

            print(f"[TJproxy] <<< response ({len(full_content)} chars)",
                  flush=True)

            # Build and send the complete OpenAI response
            response = _make_openai_response(chat_id, created, full_content)
            await _send_http(writer, 200, "OK", body=response.encode(),
                             extra_headers={"Content-Type": "application/json"})

    finally:
        if not bridge_finished:
            await _cancel_bridge_request(request_id)
        if _pending_request_id == request_id:
            _pending_queue = None
            _pending_request_id = None


# ---------------------------------------------------------------------------
# WebSocket upgrade handler
# ---------------------------------------------------------------------------


async def _handle_ws(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     headers: dict, query: str) -> bool:
    """Perform WebSocket upgrade, then relay Extension messages to HTTP."""
    global _ws_writer, _pending_queue, _pending_request_id, _bridge_token

    origin = headers.get("origin", "")
    print(f"[TJproxy] WS origin: {origin!r}", flush=True)
    if origin and not origin.startswith("chrome-extension://") and not origin.startswith("http://localhost"):
        await _send_http(writer, 403, "Forbidden")
        return False

    tokens = parse_qs(query).get("token", [])
    token = tokens[0] if len(tokens) == 1 else ""
    if not token or (_bridge_token is not None and token != _bridge_token):
        await _send_http(writer, 403, "Forbidden")
        return False

    if _ws_writer is not None and not _ws_writer.is_closing():
        await _send_http(writer, 409, "Conflict")
        return False

    # Handshake
    key = headers.get("sec-websocket-key", "")
    if not key:
        await _send_http(writer, 400, "Bad Request")
        return False
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

    _bridge_token = token
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
                request_id = msg.get("request_id")
                if (q is not None and
                        (request_id is None or
                         request_id == _pending_request_id)):
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

    return True


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
        if len(request_line) > MAX_REQUEST_LINE:
            await _send_http(writer, 414, "URI Too Long")
            return

        parts = request_line.decode(errors="replace").strip().split()
        if len(parts) != 3:
            await _send_http(writer, 400, "Bad Request")
            return
        method, target = parts[0].upper(), parts[1]
        parsed_target = urlsplit(target)
        path = parsed_target.path

        # Read headers
        headers: dict[str, str] = {}
        header_bytes = 0
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            header_bytes += len(line)
            if header_bytes > MAX_HEADER_BYTES:
                await _send_http(writer, 431, "Request Header Fields Too Large")
                return
            line_str = line.decode(errors="replace").strip()
            if ":" not in line_str:
                await _send_http(writer, 400, "Bad Request")
                return
            k, v = line_str.split(":", 1)
            headers[k.strip().lower()] = v.strip()

        # Route
        upgrade = headers.get("upgrade", "").lower()
        if upgrade == "websocket":
            if path != WS_PATH:
                await _send_http(writer, 404, "Not Found")
            else:
                is_websocket = await _handle_ws(
                    reader, writer, headers, parsed_target.query)
        elif method == "POST" and path == "/chat":
            async with _chat_lock:
                await _handle_chat(reader, writer, headers)
        elif method == "POST" and path == "/v1/chat/completions":
            async with _chat_lock:
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
    except Exception as exc:
        peer = writer.get_extra_info("peername")
        print(f"[TJproxy] connection error from {peer}: {exc}",
              file=sys.stderr, flush=True)
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
