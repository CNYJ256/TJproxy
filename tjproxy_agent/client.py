from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

import requests


class ServiceError(RuntimeError):
    """Raised when TJproxy cannot be reached or managed safely."""


class TJproxyClient:
    def __init__(self, base_url: str, *, request_timeout: float):
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout

    def is_compatible(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=0.5)
            response.raise_for_status()
            models = response.json().get("data", [])
            return any(
                isinstance(model, dict) and model.get("id") == "tongji-agent"
                for model in models
            )
        except (requests.RequestException, ValueError, AttributeError, TypeError):
            return False

    def port_is_open(self) -> bool:
        parsed = urlsplit(self.base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            return False

    def complete(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": "tongji-agent",
            "messages": messages,
            "stream": False,
        }
        response = None
        for attempt in range(2):
            try:
                response = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    timeout=self.request_timeout,
                )
                break
            except requests.ConnectionError as exc:
                if attempt == 1 or not self.is_compatible():
                    raise ServiceError("TJproxy connection lost") from exc
            except requests.RequestException as exc:
                raise ServiceError(f"TJproxy request failed: {exc}") from exc

        assert response is not None
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ServiceError(f"TJproxy returned HTTP {response.status_code}") from exc
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ServiceError("invalid completion response") from exc
        if not isinstance(content, str):
            raise ServiceError("invalid completion response")
        return content


class ServiceManager:
    def __init__(
        self,
        server_path: Path,
        base_url: str,
        startup_timeout: float,
        *,
        request_timeout: float = 330,
    ):
        self.server_path = server_path.resolve()
        self.client = TJproxyClient(base_url, request_timeout=request_timeout)
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen[str] | None = None

    @property
    def owns_process(self) -> bool:
        return self.process is not None

    def ensure_running(self) -> TJproxyClient:
        if self.client.is_compatible():
            return self.client
        if self.client.port_is_open():
            raise ServiceError(
                "configured port is occupied by an incompatible service"
            )
        if not self.server_path.is_file():
            raise ServiceError(f"TJproxy server not found: {self.server_path}")

        parsed = urlsplit(self.client.base_url)
        env = os.environ.copy()
        env["TJPROXY_PORT"] = str(
            parsed.port or (443 if parsed.scheme == "https" else 80)
        )
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(self.server_path)],
                cwd=self.server_path.parent,
                env=env,
                stdout=None,
                stderr=None,
                text=True,
            )
        except OSError as exc:
            raise ServiceError(f"cannot start TJproxy: {exc}") from exc

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            assert self.process is not None
            if self.process.poll() is not None:
                return_code = self.process.returncode
                self.close()
                raise ServiceError(f"TJproxy exited with {return_code}")
            if self.client.is_compatible():
                return self.client
            time.sleep(0.1)

        self.close()
        raise ServiceError("TJproxy readiness timeout")

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            else:
                process.wait(timeout=1)
        finally:
            self.process = None
