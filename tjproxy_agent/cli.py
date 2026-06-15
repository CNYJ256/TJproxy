from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import TextIO

from .client import ServiceError, ServiceManager
from .config import ConfigError, load_config
from .powershell import PowerShellExecutor
from .prompt import load_system_prompt
from .protocol import ToolCall
from .runner import AgentRunner, ToolDispatcher
from .workspace import ToolFailure, Workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TJproxy local agent harness")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "agent.toml",
    )
    return parser


def interactive_loop(
    runner: AgentRunner, *, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout
) -> int:
    runner.audit = _audit_printer(stdout)
    print("TJproxy agent CLI. /new resets context; /exit quits.", file=stdout)
    print(
        "Safety: workspace policy is application-level, not an OS sandbox.",
        file=stdout,
    )
    while True:
        try:
            stdout.write("agent> ")
            stdout.flush()
            line = stdin.readline()
        except KeyboardInterrupt:
            print("", file=stdout)
            return 130
        if line == "":
            return 0
        task = line.strip()
        if not task:
            continue
        if task == "/exit":
            return 0
        if task == "/new":
            runner.clear_history()
            print("context cleared", file=stdout)
            continue
        try:
            outcome = runner.run(task)
        except KeyboardInterrupt:
            print("current task cancelled", file=stdout)
            continue
        except ServiceError as exc:
            print(f"current task failed: {exc}", file=stdout)
            continue
        print(outcome.content, file=stdout)


def _audit_printer(stdout: TextIO):
    def emit(event: tuple[str, object]) -> None:
        kind, value = event
        if kind == "tool_call" and isinstance(value, ToolCall):
            print(_tool_call_summary(value), file=stdout)
            return
        if kind != "tool_result" or not isinstance(value, str):
            return
        try:
            result = json.loads(value)
        except json.JSONDecodeError:
            print("[tool result] invalid result envelope", file=stdout)
            return
        print(
            "[tool result] "
            f"{result.get('tool')} ok={result.get('ok')} "
            f"exit={result.get('exit_code')} error={result.get('error_code')} "
            f"truncated={result.get('truncated')}",
            file=stdout,
        )
        for label in ("stdout", "stderr"):
            text = result.get(label)
            if isinstance(text, str) and text:
                print(f"[{label}] {text[:500]}", file=stdout)

    return emit


def _tool_call_summary(call: ToolCall) -> str:
    if call.tool == "read":
        return f"[tool] read {call.arguments['path']}"
    if call.tool == "write":
        return (
            f"[tool] write {call.arguments['path']} "
            f"({len(call.arguments['content'])} chars)"
        )
    if call.tool == "edit":
        return f"[tool] edit {call.arguments['path']}"
    commands = [
        stage.get("command", "?") for stage in call.arguments.get("pipeline", [])
    ]
    return f"[tool] powershell {' | '.join(commands)}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manager: ServiceManager | None = None
    try:
        config = load_config(args.config)
        workspace = Workspace(
            args.workspace,
            read_limit=config.limits.read_bytes,
            write_limit=config.limits.write_bytes,
        )
        shell = PowerShellExecutor(
            workspace,
            config.powershell,
            command_chars=config.limits.command_chars,
            output_chars=config.limits.output_chars,
        )
        manager = ServiceManager(
            Path(__file__).resolve().parents[1] / "server" / "main.py",
            config.service.base_url,
            config.service.startup_timeout_seconds,
            request_timeout=config.service.request_timeout_seconds,
        )
        client = manager.ensure_running()
        tools = ToolDispatcher(
            workspace, shell, output_chars=config.limits.output_chars
        )
        system_prompt = load_system_prompt(
            args.config.resolve(), config.agent.prompt_path
        )
        runner = AgentRunner(
            client,
            tools,
            max_rounds=config.agent.max_rounds,
            system_prompt=system_prompt,
        )
        return interactive_loop(runner)
    except (ConfigError, ServiceError, ToolFailure, OSError, ValueError) as exc:
        print(f"agent startup failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if manager is not None:
            manager.close()
