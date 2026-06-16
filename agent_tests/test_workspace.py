from pathlib import Path

import pytest

from tjproxy_agent.workspace import ToolFailure, Workspace


def make_workspace(path: Path, *, read_limit: int = 1000, write_limit: int = 1000):
    return Workspace(path, read_limit=read_limit, write_limit=write_limit)


def test_read_write_and_exact_edit(tmp_path: Path):
    workspace = make_workspace(tmp_path)

    workspace.write("notes/a.txt", "alpha beta")
    assert workspace.read("notes/a.txt") == "alpha beta"
    count = workspace.edit(
        "notes/a.txt", "beta", "gamma", expected_replacements=1
    )

    assert count == 1
    assert (tmp_path / "notes/a.txt").read_text(encoding="utf-8") == "alpha gamma"


@pytest.mark.parametrize(
    "path", ["../outside.txt", "C:/Windows/win.ini", "a.txt:secret", ""]
)
def test_rejects_lexical_escape_and_ads(tmp_path: Path, path: str):
    workspace = make_workspace(tmp_path)

    with pytest.raises(ToolFailure, match="WORKSPACE_ESCAPE"):
        workspace.read(path)


def test_edit_rejects_ambiguous_match(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x x", encoding="utf-8")
    workspace = make_workspace(tmp_path)

    with pytest.raises(ToolFailure, match="EDIT_CONFLICT"):
        workspace.edit("a.txt", "x", "y", expected_replacements=1)


def test_symlink_escape_is_rejected(tmp_path: Path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    workspace = make_workspace(tmp_path)

    with pytest.raises(ToolFailure, match="WORKSPACE_ESCAPE"):
        workspace.write("link/escaped.txt", "no")


def test_rejects_read_and_write_limits(tmp_path: Path):
    (tmp_path / "large.txt").write_text("12345", encoding="utf-8")
    workspace = make_workspace(tmp_path, read_limit=4, write_limit=4)

    with pytest.raises(ToolFailure, match="READ_LIMIT"):
        workspace.read("large.txt")
    with pytest.raises(ToolFailure, match="WRITE_LIMIT"):
        workspace.write("new.txt", "12345")


def test_rejects_non_utf8_and_directories(tmp_path: Path):
    (tmp_path / "binary.bin").write_bytes(b"\xff")
    workspace = make_workspace(tmp_path)

    with pytest.raises(ToolFailure, match="NOT_UTF8"):
        workspace.read("binary.bin")
    with pytest.raises(ToolFailure, match="NOT_A_FILE"):
        workspace.read(".")


def test_missing_read_is_reported(tmp_path: Path):
    workspace = make_workspace(tmp_path)

    with pytest.raises(ToolFailure, match="NOT_FOUND"):
        workspace.read("missing.txt")


@pytest.mark.parametrize("path", ["NUL", "con.txt", "dir/COM1.log", "LPT9"])
def test_windows_device_names_are_rejected(tmp_path: Path, path: str):
    workspace = make_workspace(tmp_path)

    with pytest.raises(ToolFailure, match="WORKSPACE_ESCAPE"):
        workspace.write(path, "data")


def test_list_dir_returns_workspace_entries_with_metadata(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    workspace = make_workspace(tmp_path)

    result = workspace.list_dir(".")

    assert "README.md\tfile\t5 bytes" in result
    assert "pkg\tdir" in result


def test_read_range_returns_numbered_inclusive_lines_without_full_read_limit(tmp_path: Path):
    (tmp_path / "large.txt").write_text(
        "\n".join(f"line {number}" for number in range(1, 101)),
        encoding="utf-8",
    )
    workspace = make_workspace(tmp_path, read_limit=40)

    result = workspace.read_range("large.txt", 10, 12)

    assert result == "10 | line 10\n11 | line 11\n12 | line 12"


def test_search_returns_matching_relative_paths_and_line_numbers(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "runner.py").write_text(
        "class AgentRunner:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    workspace = make_workspace(tmp_path)

    result = workspace.search("AgentRunner", ".")

    assert "pkg/runner.py:1 | class AgentRunner:" in result
    assert "notes.txt" not in result


def test_project_map_summarizes_source_files(tmp_path: Path):
    (tmp_path / "runner.py").write_text(
        "class AgentRunner:\n    pass\n\ndef parse_response():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text("[limits]\nread_bytes = 100\n", encoding="utf-8")
    workspace = make_workspace(tmp_path)

    result = workspace.project_map()

    assert "runner.py" in result
    assert "- class AgentRunner" in result
    assert "- def parse_response()" in result
    assert "language: Python" in result
    assert "1 | class AgentRunner:" in result
    assert "config.toml" in result


def test_context_pack_returns_query_focused_numbered_snippets(tmp_path: Path):
    (tmp_path / "runner.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "class ToolDispatcher:",
                "    pass",
                "",
                "class AgentRunner:",
                "    def run(self):",
                "        return 'ok'",
            ]
        ),
        encoding="utf-8",
    )
    workspace = make_workspace(tmp_path)

    result = workspace.context_pack(["runner.py"], "AgentRunner")

    assert "# runner.py" in result
    assert "L3-L8" in result
    assert "6 | class AgentRunner:" in result
    assert "1 | from __future__" not in result
