from __future__ import annotations

import ast
import os
from pathlib import Path, PureWindowsPath
import stat
import tempfile

SKIP_DIR_NAMES = {
    ".git",
    ".pytest_cache",
    ".playwright-mcp",
    "__pycache__",
    "node_modules",
    "dist",
}
INDEX_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".md",
    ".json",
    ".toml",
    ".txt",
    ".html",
    ".css",
}
LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".md": "Markdown",
    ".json": "JSON",
    ".toml": "TOML",
    ".txt": "Text",
    ".html": "HTML",
    ".css": "CSS",
}
MAX_SEARCH_MATCHES = 100
MAX_PROJECT_MAP_FILES = 80
MAX_CONTEXT_SNIPPETS_PER_FILE = 8
CONTEXT_RADIUS = 3

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

    def list_dir(self, raw_path: str) -> str:
        path = self.resolve(raw_path)
        if not path.is_dir():
            raise ToolFailure("NOT_A_DIR", raw_path)

        rows: list[str] = []
        try:
            children = sorted(path.iterdir(), key=lambda child: (child.is_file(), child.name.lower()))
            for child in children:
                name = child.name
                if child.is_dir():
                    rows.append(f"{name}\tdir")
                elif child.is_file():
                    rows.append(f"{name}\tfile\t{child.stat().st_size} bytes")
                else:
                    rows.append(f"{name}\tother")
        except OSError as exc:
            raise ToolFailure("LIST_FAILED", str(exc)) from exc
        return "\n".join(rows)

    def read_range(self, raw_path: str, start: int, end: int) -> str:
        if start < 1 or end < start:
            raise ToolFailure("INVALID_RANGE", raw_path)
        path = self.resolve(raw_path)
        if not path.is_file():
            raise ToolFailure("NOT_A_FILE", raw_path)

        rows: list[str] = []
        try:
            with path.open("r", encoding="utf-8", newline=None) as handle:
                for line_number, line in enumerate(handle, 1):
                    if line_number > end:
                        break
                    if line_number >= start:
                        rows.append(f"{line_number} | {line.rstrip('\r\n')}")
                        if len("\n".join(rows).encode("utf-8")) > self.read_limit:
                            raise ToolFailure("READ_LIMIT", raw_path)
        except UnicodeDecodeError as exc:
            raise ToolFailure("NOT_UTF8", raw_path) from exc
        except ToolFailure:
            raise
        except OSError as exc:
            raise ToolFailure("READ_FAILED", str(exc)) from exc
        return "\n".join(rows)

    def search(self, query: str, raw_path: str) -> str:
        if not query:
            raise ToolFailure("INVALID_QUERY", "search query cannot be empty")
        path = self.resolve(raw_path)
        files = [path] if path.is_file() else self._iter_text_candidates(path)
        matches: list[str] = []
        for file_path in files:
            try:
                with file_path.open("r", encoding="utf-8", newline=None) as handle:
                    for line_number, line in enumerate(handle, 1):
                        if query in line:
                            relative = self._relative(file_path)
                            matches.append(
                                f"{relative}:{line_number} | {line.rstrip('\r\n')}"
                            )
                            if len(matches) >= MAX_SEARCH_MATCHES:
                                matches.append("[truncated: too many matches]")
                                return "\n".join(matches)
            except UnicodeDecodeError:
                continue
            except OSError:
                continue
        return "\n".join(matches) if matches else "no matches"

    def project_map(self) -> str:
        sections: list[str] = []
        for path in self._iter_text_candidates(self.root):
            if path.suffix.lower() not in INDEX_SUFFIXES:
                continue
            if len(sections) >= MAX_PROJECT_MAP_FILES:
                sections.append("[truncated: too many files]")
                break
            sections.append(self._project_map_section(path))
        return "\n\n".join(sections) if sections else "no source files"

    def context_pack(self, paths: list[str], query: str) -> str:
        if not paths or not query:
            raise ToolFailure("INVALID_CONTEXT_PACK", "paths and query are required")
        sections: list[str] = []
        terms = [term.casefold() for term in query.split() if term]
        for raw_path in paths:
            path = self.resolve(raw_path)
            if not path.is_file():
                raise ToolFailure("NOT_A_FILE", raw_path)
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError as exc:
                raise ToolFailure("NOT_UTF8", raw_path) from exc
            except OSError as exc:
                raise ToolFailure("READ_FAILED", str(exc)) from exc
            ranges = self._context_ranges(lines, terms)
            chunks = [f"# {self._relative(path)}"]
            for start, end in ranges:
                chunks.append(f"L{start}-L{end}")
                chunks.append(_number_lines(lines[start - 1 : end], offset=start))
            sections.append("\n".join(chunks))
        result = "\n\n".join(sections)
        if len(result.encode("utf-8")) > self.read_limit:
            raise ToolFailure("READ_LIMIT", "context_pack")
        return result

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

    def _iter_text_candidates(self, root: Path):
        if root.is_file():
            yield root
            return
        if not root.is_dir():
            raise ToolFailure("NOT_A_DIR", str(root))
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                name
                for name in dirs
                if name not in SKIP_DIR_NAMES and not name.startswith(".")
            ]
            for name in sorted(files, key=str.lower):
                if name.startswith("."):
                    continue
                yield Path(current) / name

    def _project_map_section(self, path: Path) -> str:
        relative = self._relative(path)
        lines = self._first_lines(path, 20)
        section = [
            relative,
            f"size: {path.stat().st_size} bytes",
            f"language: {LANGUAGES.get(path.suffix.lower(), 'Text')}",
        ]
        symbols = self._top_level_symbols(path, lines)
        section.extend(symbols if symbols else ["- no top-level symbols"])
        section.append("first 20 lines:")
        section.append(_number_lines(lines))
        return "\n".join(section)

    def _first_lines(self, path: Path, count: int) -> list[str]:
        lines: list[str] = []
        try:
            with path.open("r", encoding="utf-8", newline=None) as handle:
                for _, line in zip(range(count), handle):
                    lines.append(line.rstrip("\r\n"))
        except UnicodeDecodeError:
            return ["[non-utf8 skipped]"]
        except OSError as exc:
            return [f"[read failed: {exc}]"]
        return lines

    def _top_level_symbols(self, path: Path, first_lines: list[str]) -> list[str]:
        if path.suffix.lower() == ".py":
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                return []
            symbols = []
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    symbols.append(f"- class {node.name}")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(f"- def {node.name}()")
            return symbols
        if path.suffix.lower() in {".js", ".ts"}:
            symbols = []
            for line in first_lines:
                stripped = line.strip()
                if stripped.startswith("class "):
                    symbols.append(f"- class {stripped.split()[1].split('{', 1)[0]}")
                elif stripped.startswith("function "):
                    symbols.append(f"- function {stripped.split()[1].split('(', 1)[0]}()")
                elif stripped.startswith("export function "):
                    symbols.append(f"- function {stripped.split()[2].split('(', 1)[0]}()")
            return symbols
        return []

    def _context_ranges(self, lines: list[str], terms: list[str]) -> list[tuple[int, int]]:
        match_lines = []
        for index, line in enumerate(lines, 1):
            folded = line.casefold()
            if any(term in folded for term in terms):
                match_lines.append(index)
        if not match_lines:
            return [(1, min(len(lines), 80))] if lines else []
        ranges: list[tuple[int, int]] = []
        for line_number in match_lines[:MAX_CONTEXT_SNIPPETS_PER_FILE]:
            start = max(1, line_number - CONTEXT_RADIUS)
            end = min(len(lines), line_number + CONTEXT_RADIUS)
            if ranges and start <= ranges[-1][1] + 1:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
            else:
                ranges.append((start, end))
        return ranges

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def _is_windows_device_name(part: str) -> bool:
    normalized = part.rstrip(" .")
    stem = normalized.split(".", 1)[0].upper()
    return stem in WINDOWS_RESERVED_NAMES


def _number_lines(lines: list[str], *, offset: int = 1) -> str:
    return "\n".join(
        f"{line_number} | {line}"
        for line_number, line in enumerate(lines, offset)
    )
