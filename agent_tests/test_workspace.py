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
