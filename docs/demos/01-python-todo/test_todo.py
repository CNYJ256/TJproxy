import json
import pytest
from pathlib import Path
from todo import load_todos, save_todos, add, list_todos, done, delete, DATA_FILE


@pytest.fixture(autouse=True)
def clear_data():
    """Remove the JSON file before each test."""
    if DATA_FILE.exists():
        DATA_FILE.unlink()
    yield
    if DATA_FILE.exists():
        DATA_FILE.unlink()


def test_add_and_list(capsys):
    add("buy milk")
    list_todos()
    captured = capsys.readouterr()
    assert "Added todo: buy milk" in captured.out
    assert "1. [ ] buy milk" in captured.out


def test_list_empty(capsys):
    list_todos()
    captured = capsys.readouterr()
    assert "No todos." in captured.out


def test_done(capsys):
    add("read book")
    done(1)
    captured = capsys.readouterr()
    assert "Marked todo 1 as done." in captured.out
    # Verify via list
    list_todos()
    captured = capsys.readouterr()
    assert "[x] read book" in captured.out


def test_done_not_found(capsys):
    done(99)
    captured = capsys.readouterr()
    assert "Todo 99 not found." in captured.out


def test_delete(capsys):
    add("task1")
    add("task2")
    delete(1)
    captured = capsys.readouterr()
    assert "Deleted todo 1." in captured.out
    list_todos()
    captured = capsys.readouterr()
    assert "2. [ ] task2" in captured.out


def test_delete_not_found(capsys):
    delete(99)
    captured = capsys.readouterr()
    assert "Todo 99 not found." in captured.out


def test_persistence():
    add("persist")
    todos = load_todos()
    assert len(todos) == 1
    assert todos[0]["description"] == "persist"
    assert todos[0]["done"] is False


def test_id_after_delete():
    add("a")
    add("b")
    delete(1)
    add("c")
    todos = load_todos()
    ids = [t["id"] for t in todos]
    assert ids == [2, 3]  # IDs are not reassigned
