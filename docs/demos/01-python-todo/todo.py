#!/usr/bin/env python3
"""A simple CLI todo tool using JSON file storage."""

import json
import sys
from pathlib import Path

DATA_FILE = Path(__file__).parent / "todos.json"


def load_todos():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_todos(todos):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(todos, f, indent=2)


def add(description):
    todos = load_todos()
    max_id = max((t["id"] for t in todos), default=0)
    todo = {"id": max_id + 1, "description": description, "done": False}
    todos.append(todo)
    save_todos(todos)
    print(f"Added todo: {description}")


def list_todos():
    todos = load_todos()
    if not todos:
        print("No todos.")
        return
    for t in todos:
        status = "[x]" if t["done"] else "[ ]"
        print(f"{t['id']}. {status} {t['description']}")


def done(todo_id):
    todos = load_todos()
    for t in todos:
        if t["id"] == todo_id:
            t["done"] = True
            save_todos(todos)
            print(f"Marked todo {todo_id} as done.")
            return
    print(f"Todo {todo_id} not found.")


def delete(todo_id):
    todos = load_todos()
    new_todos = [t for t in todos if t["id"] != todo_id]
    if len(new_todos) == len(todos):
        print(f"Todo {todo_id} not found.")
        return
    save_todos(new_todos)
    print(f"Deleted todo {todo_id}.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python todo.py <command> [args]")
        print("Commands: add, list, done, delete")
        sys.exit(1)

    command = sys.argv[1]
    if command == "add":
        if len(sys.argv) < 3:
            print("Usage: python todo.py add <description>")
            sys.exit(1)
        add(" ".join(sys.argv[2:]))
    elif command == "list":
        list_todos()
    elif command == "done":
        if len(sys.argv) < 3:
            print("Usage: python todo.py done <id>")
            sys.exit(1)
        done(int(sys.argv[2]))
    elif command == "delete":
        if len(sys.argv) < 3:
            print("Usage: python todo.py delete <id>")
            sys.exit(1)
        delete(int(sys.argv[2]))
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
