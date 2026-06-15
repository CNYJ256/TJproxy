from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import PureWindowsPath
import re
import subprocess
from typing import Any

from .config import CommandPolicy, PowerShellConfig
from .workspace import ToolFailure, Workspace

FORBIDDEN_TOKENS = (";", "&&", "||", ">", "<", "`", "$(", "${", "@{", "$env:")
TRAVERSAL_PATTERN = re.compile(r"(^|[=\\/])\.\.($|[\\/])")
SWITCH_PATTERN = re.compile(r"^--?[A-Za-z][A-Za-z0-9-]*$")
ALLOWED_PYTHON_MODULES = {"pytest", "unittest", "compileall"}


class ShellFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ShellResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False
    error_code: str | None = None


def quote_ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quote_ps_argument(value: str) -> str:
    if SWITCH_PATTERN.fullmatch(value):
        return value
    return quote_ps(value)


class PowerShellExecutor:
    def __init__(
        self,
        workspace: Workspace,
        config: PowerShellConfig,
        *,
        command_chars: int,
        output_chars: int,
    ):
        self.workspace = workspace
        self.config = config
        self.command_chars = command_chars
        self.output_chars = output_chars
        self.policies = {
            policy.name.casefold(): policy for policy in config.commands
        }

    def _compile(self, pipeline: list[dict[str, Any]]) -> str:
        if not 1 <= len(pipeline) <= self.config.max_pipeline_stages:
            raise ShellFailure("PIPELINE_LIMIT", str(len(pipeline)))
        commands = [
            self._compile_stage(stage, index)
            for index, stage in enumerate(pipeline)
        ]
        script = " | ".join(commands)
        if len(script) > self.command_chars:
            raise ShellFailure("COMMAND_LIMIT", str(len(script)))
        return script

    def _compile_stage(self, stage: dict[str, Any], index: int) -> str:
        if not isinstance(stage, dict) or set(stage) != {"command", "args"}:
            raise ShellFailure("INVALID_STAGE", str(index))
        command = stage["command"]
        args = stage["args"]
        if not isinstance(command, str) or not command:
            raise ShellFailure("INVALID_COMMAND", str(command))
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ShellFailure("INVALID_ARGUMENTS", command)

        self._reject_syntax(command)
        policy = self.policies.get(command.casefold())
        if policy is None:
            raise ShellFailure("COMMAND_NOT_ALLOWED", command)
        if index > 0 and not policy.allow_in_pipeline:
            raise ShellFailure("PIPELINE_NOT_ALLOWED", command)

        checked_args = list(args)
        for arg in checked_args:
            self._reject_syntax(arg)
            self._reject_path_escape(arg)
        if any(arg in policy.denied_args for arg in checked_args):
            raise ShellFailure("ARGUMENT_NOT_ALLOWED", command)

        self._check_existing_path_arguments(checked_args)
        self._validate_subcommand(policy, checked_args)
        checked_args = self._resolve_script(command, checked_args, policy)
        if command.casefold() == "pwsh" and checked_args[0] == "-File":
            return "& " + " ".join(
                quote_ps_argument(arg) for arg in checked_args[1:]
            )
        return "& " + " ".join(
            [quote_ps(policy.name), *(quote_ps_argument(arg) for arg in checked_args)]
        )

    def _validate_subcommand(
        self, policy: CommandPolicy, args: list[str]
    ) -> None:
        if not policy.allowed_subcommands:
            return
        if not args:
            raise ShellFailure("SUBCOMMAND_REQUIRED", policy.name)
        first = args[0]
        is_script = policy.script_runner and first.lower().endswith(
            (".py", ".js", ".ps1")
        )
        if first not in policy.allowed_subcommands and not is_script:
            raise ShellFailure("SUBCOMMAND_NOT_ALLOWED", first)

    def _resolve_script(
        self, command: str, args: list[str], policy: CommandPolicy
    ) -> list[str]:
        if not policy.script_runner or not args:
            return args

        folded = command.casefold()
        if folded == "python" and args[0] == "-m":
            if len(args) < 2 or args[1] not in ALLOWED_PYTHON_MODULES:
                module = args[1] if len(args) > 1 else ""
                raise ShellFailure("MODULE_NOT_ALLOWED", module)
            return args

        script_index = 1 if folded == "pwsh" and args[0] == "-File" else 0
        if script_index >= len(args):
            raise ShellFailure("SCRIPT_REQUIRED", command)
        raw_script = args[script_index]
        try:
            resolved = self.workspace.resolve(raw_script)
        except ToolFailure as exc:
            raise ShellFailure(exc.code, exc.message) from exc
        if not resolved.is_file():
            raise ShellFailure("SCRIPT_NOT_FOUND", raw_script)
        updated = list(args)
        updated[script_index] = str(resolved)
        return updated

    def _check_existing_path_arguments(self, args: list[str]) -> None:
        for arg in args:
            if arg.startswith("-"):
                continue
            candidate = self.workspace.root.joinpath(*PureWindowsPath(arg).parts)
            if not candidate.exists():
                continue
            try:
                self.workspace.resolve(arg)
            except ToolFailure as exc:
                raise ShellFailure(exc.code, exc.message) from exc

    @staticmethod
    def _reject_syntax(value: str) -> None:
        if not value or any(token in value for token in FORBIDDEN_TOKENS):
            raise ShellFailure("SHELL_SYNTAX_REJECTED", value)

    @staticmethod
    def _reject_path_escape(value: str) -> None:
        path = PureWindowsPath(value)
        if (
            path.is_absolute()
            or path.drive
            or value.startswith(("\\\\", "/"))
            or ":" in value
            or TRAVERSAL_PATTERN.search(value)
        ):
            raise ShellFailure("WORKSPACE_ESCAPE", value)

    def run(self, pipeline: list[dict[str, Any]]) -> ShellResult:
        script = self._compile(pipeline)
        command = [
            self.config.executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=self.workspace.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except OSError as exc:
            raise ShellFailure("EXECUTABLE_NOT_FOUND", str(exc)) from exc

        timed_out = False
        try:
            stdout, stderr = process.communicate(
                timeout=self.config.timeout_seconds
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
        except KeyboardInterrupt:
            _terminate_process_tree(process)
            process.communicate()
            raise

        stdout, out_cut = _truncate(stdout, self.output_chars)
        stderr, err_cut = _truncate(stderr, self.output_chars)
        return ShellResult(
            None if timed_out else process.returncode,
            stdout,
            stderr,
            timed_out=timed_out,
            truncated=out_cut or err_cut,
            error_code=(
                "COMMAND_TIMEOUT"
                if timed_out
                else None
                if process.returncode == 0
                else "COMMAND_FAILED"
            ),
        )


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.kill()
