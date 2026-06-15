from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import stat
import tempfile

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class ToolFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Workspace:
    def __init__(self, root: Path, *, read_limit: int, write_limit: int):
        try:
            self.root = root.resolve(strict=True)
        except OSError as exc:
            raise ToolFailure("INVALID_WORKSPACE", str(root)) from exc
        if not self.root.is_dir():
            raise ToolFailure("INVALID_WORKSPACE", str(root))
        self.read_limit = read_limit
        self.write_limit = write_limit

    def resolve(self, raw_path: str, *, for_write: bool = False) -> Path:
        windows = PureWindowsPath(raw_path)
        if (
            not raw_path
            or windows.is_absolute()
            or windows.drive
            or ":" in raw_path
            or ".." in windows.parts
            or any(_is_windows_device_name(part) for part in windows.parts)
        ):
            raise ToolFailure("WORKSPACE_ESCAPE", raw_path)

        candidate = self.root.joinpath(*windows.parts)
        existing = candidate
        missing_parts: list[str] = []
        while not existing.exists():
            if existing == self.root:
                break
            missing_parts.append(existing.name)
            existing = existing.parent

        try:
            self._reject_reparse_components(existing)
            resolved = existing.resolve(strict=True)
        except OSError as exc:
            raise ToolFailure("WORKSPACE_ESCAPE", raw_path) from exc
        if not resolved.is_relative_to(self.root):
            raise ToolFailure("WORKSPACE_ESCAPE", raw_path)
        for part in reversed(missing_parts):
            resolved /= part
        if not for_write and not resolved.exists():
            raise ToolFailure("NOT_FOUND", raw_path)
        return resolved

    def _reject_reparse_components(self, existing: Path) -> None:
        try:
            relative = existing.relative_to(self.root)
        except ValueError as exc:
            raise ToolFailure("WORKSPACE_ESCAPE", str(existing)) from exc
        current = self.root
        for part in relative.parts:
            current /= part
            attributes = getattr(os.lstat(current), "st_file_attributes", 0)
            if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ToolFailure("WORKSPACE_ESCAPE", str(current))

    def read(self, raw_path: str) -> str:
        path = self.resolve(raw_path)
        if not path.is_file():
            raise ToolFailure("NOT_A_FILE", raw_path)
        try:
            size = path.stat().st_size
            if size > self.read_limit:
                raise ToolFailure("READ_LIMIT", raw_path)
            data = path.read_bytes()
        except ToolFailure:
            raise
        except OSError as exc:
            raise ToolFailure("READ_FAILED", str(exc)) from exc
        if len(data) > self.read_limit:
            raise ToolFailure("READ_LIMIT", raw_path)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolFailure("NOT_UTF8", raw_path) from exc

    def write(self, raw_path: str, content: str) -> None:
        data = content.encode("utf-8")
        if len(data) > self.write_limit:
            raise ToolFailure("WRITE_LIMIT", raw_path)
        path = self.resolve(raw_path, for_write=True)
        try:
            if path.exists() and not path.is_file():
                raise ToolFailure("NOT_A_FILE", raw_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_replace(path, data)
        except ToolFailure:
            raise
        except OSError as exc:
            raise ToolFailure("WRITE_FAILED", str(exc)) from exc

    def edit(
        self,
        raw_path: str,
        old_text: str,
        new_text: str,
        *,
        expected_replacements: int = 1,
    ) -> int:
        original = self.read(raw_path)
        actual = original.count(old_text)
        if actual != expected_replacements:
            raise ToolFailure(
                "EDIT_CONFLICT",
                f"expected {expected_replacements} replacements, found {actual}",
            )
        updated = original.replace(old_text, new_text, expected_replacements)
        self.write(raw_path, updated)
        return actual

    @staticmethod
    def _atomic_replace(path: Path, data: bytes) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def _is_windows_device_name(part: str) -> bool:
    normalized = part.rstrip(" .")
    stem = normalized.split(".", 1)[0].upper()
    return stem in WINDOWS_RESERVED_NAMES
