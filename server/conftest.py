"""pytest fixtures for TJproxy server tests.

These fixtures run against the actual main.py server when it exists.
When main.py is not yet implemented, the tests serve as executable specs —
they show exactly what the server interface should look like.
"""

import asyncio
import json
import sys

import pytest
import requests
import websockets
from websockets.asyncio.client import ClientConnection


# ---------------------------------------------------------------------------
# Helpers to bring up the real server when main.py is available
# ---------------------------------------------------------------------------

SERVER_HOST = "localhost"
SERVER_PORT = 8765
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
WS_URL = f"ws://{SERVER_HOST}:{SERVER_PORT}"


@pytest.fixture(scope="session")
def anyio_backend():
    """Tell pytest-asyncio to use the asyncio backend (anyio is fine too)."""
    return "asyncio"


# ---------------------------------------------------------------------------
# Fixture: start the real server for end-to-end tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def server():
    """Start main.py as a subprocess, wait until it is ready, then yield.

    If main.py does not exist this fixture is skipped — the tests still
    document the contract but cannot run end-to-end.
    """
    import os
    import subprocess
    import time
    from pathlib import Path

    server_dir = Path(__file__).resolve().parent
    main_py = server_dir / "main.py"

    if not main_py.exists():
        pytest.skip("main.py not yet implemented — tests serve as spec only")

    proc = subprocess.Popen(
        [sys.executable, str(main_py)],
        cwd=str(server_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready (max 10 s)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            requests.get(SERVER_URL, timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        proc.wait()
        pytest.fail("Server did not start within 10 seconds")

    yield

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Low-level WS client helper used by multiple tests
# ---------------------------------------------------------------------------


async def _ws_send_and_expect(ws: ClientConnection, payload: dict, expected_type: str) -> dict:
    """Send *payload* on *ws* and return the first JSON message whose 'type'
    matches *expected_type*.  A timeout of 3 s is enforced."""
    await ws.send(json.dumps(payload))
    async with asyncio.timeout(3):
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == expected_type:
                return msg
    raise TimeoutError(f"Did not receive {expected_type!r} message")
