from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tomllib

HARD_MAX_ROUNDS = 64


class ConfigError(ValueError):
    """Raised when the agent TOML is invalid."""


@dataclass(frozen=True)
class AgentConfig:
    max_rounds: int = 32
    prompt_path: str | None = None


@dataclass(frozen=True)
class ServiceConfig:
    base_url: str = "http://localhost:8765"
    startup_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 330.0


@dataclass(frozen=True)
class LimitConfig:
    read_bytes: int = 1_000_000
    write_bytes: int = 1_000_000
    output_chars: int = 20_000
    command_chars: int = 4_000


@dataclass(frozen=True)
class CommandPolicy:
    name: str
    allowed_subcommands: tuple[str, ...] = ()
    denied_args: tuple[str, ...] = ()
    allow_in_pipeline: bool = True
    script_runner: bool = False


@dataclass(frozen=True)
class PowerShellConfig:
    executable: str = "pwsh"
    timeout_seconds: float = 60.0
    max_pipeline_stages: int = 4
    commands: tuple[CommandPolicy, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AppConfig:
    agent: AgentConfig = field(default_factory=AgentConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    limits: LimitConfig = field(default_factory=LimitConfig)
    powershell: PowerShellConfig = field(default_factory=PowerShellConfig)


SECTION_KEYS = {
    "agent": {"max_rounds", "prompt_path"},
    "service": {
        "base_url",
        "startup_timeout_seconds",
        "request_timeout_seconds",
    },
    "limits": {"read_bytes", "write_bytes", "output_chars", "command_chars"},
    "powershell": {
        "executable",
        "timeout_seconds",
        "max_pipeline_stages",
        "commands",
    },
}
COMMAND_KEYS = {
    "name",
    "allowed_subcommands",
    "denied_args",
    "allow_in_pipeline",
    "script_runner",
}


def load_config(path: Path) -> AppConfig:
    """Load a strict TOML configuration, or defaults when it is absent."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config: {exc}") from exc

    _reject_unknown_keys(raw)
    config = _build_config(raw)
    _validate_config(config)
    return config


def _reject_unknown_keys(raw: dict[str, Any]) -> None:
    for section in raw:
        if section not in SECTION_KEYS:
            raise ConfigError(f"unknown section: {section}")
    for section, allowed in SECTION_KEYS.items():
        value = raw.get(section, {})
        if not isinstance(value, dict):
            raise ConfigError(f"{section} must be a table")
        for key in value:
            if key not in allowed:
                raise ConfigError(f"unknown key: {section}.{key}")

    commands = raw.get("powershell", {}).get("commands", [])
    if not isinstance(commands, list):
        raise ConfigError("powershell.commands must be an array of tables")
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            raise ConfigError(f"powershell.commands[{index}] must be a table")
        unknown = set(command) - COMMAND_KEYS
        if unknown:
            key = sorted(unknown)[0]
            raise ConfigError(f"unknown key: powershell.commands[{index}].{key}")


def _number(value: Any, label: str, *, integer: bool = False) -> int | float:
    expected = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected):
        raise ConfigError(f"{label} has invalid type")
    return value


def _string(value: Any, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} must be a non-empty string")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be a boolean")
    return value


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{label} must be an array of strings")
    return tuple(value)


def _build_config(raw: dict[str, Any]) -> AppConfig:
    agent = raw.get("agent", {})
    service = raw.get("service", {})
    limits = raw.get("limits", {})
    shell = raw.get("powershell", {})

    policies: list[CommandPolicy] = []
    names: set[str] = set()
    for index, item in enumerate(shell.get("commands", [])):
        name = _string(item.get("name"), f"powershell.commands[{index}].name")
        assert name is not None
        folded = name.casefold()
        if folded in names:
            raise ConfigError(f"duplicate command policy: {name}")
        names.add(folded)
        policies.append(
            CommandPolicy(
                name=name,
                allowed_subcommands=_string_tuple(
                    item.get("allowed_subcommands", []),
                    f"powershell.commands[{index}].allowed_subcommands",
                ),
                denied_args=_string_tuple(
                    item.get("denied_args", []),
                    f"powershell.commands[{index}].denied_args",
                ),
                allow_in_pipeline=_boolean(
                    item.get("allow_in_pipeline", True),
                    f"powershell.commands[{index}].allow_in_pipeline",
                ),
                script_runner=_boolean(
                    item.get("script_runner", False),
                    f"powershell.commands[{index}].script_runner",
                ),
            )
        )

    return AppConfig(
        agent=AgentConfig(
            max_rounds=int(
                _number(agent.get("max_rounds", 32), "agent.max_rounds", integer=True)
            ),
            prompt_path=_string(
                agent.get("prompt_path"), "agent.prompt_path", optional=True
            ),
        ),
        service=ServiceConfig(
            base_url=str(
                _string(
                    service.get("base_url", "http://localhost:8765"),
                    "service.base_url",
                )
            ),
            startup_timeout_seconds=float(
                _number(
                    service.get("startup_timeout_seconds", 10.0),
                    "service.startup_timeout_seconds",
                )
            ),
            request_timeout_seconds=float(
                _number(
                    service.get("request_timeout_seconds", 330.0),
                    "service.request_timeout_seconds",
                )
            ),
        ),
        limits=LimitConfig(
            read_bytes=int(
                _number(limits.get("read_bytes", 1_000_000), "limits.read_bytes", integer=True)
            ),
            write_bytes=int(
                _number(
                    limits.get("write_bytes", 1_000_000),
                    "limits.write_bytes",
                    integer=True,
                )
            ),
            output_chars=int(
                _number(
                    limits.get("output_chars", 20_000),
                    "limits.output_chars",
                    integer=True,
                )
            ),
            command_chars=int(
                _number(
                    limits.get("command_chars", 4_000),
                    "limits.command_chars",
                    integer=True,
                )
            ),
        ),
        powershell=PowerShellConfig(
            executable=str(
                _string(shell.get("executable", "pwsh"), "powershell.executable")
            ),
            timeout_seconds=float(
                _number(
                    shell.get("timeout_seconds", 60.0),
                    "powershell.timeout_seconds",
                )
            ),
            max_pipeline_stages=int(
                _number(
                    shell.get("max_pipeline_stages", 4),
                    "powershell.max_pipeline_stages",
                    integer=True,
                )
            ),
            commands=tuple(policies),
        ),
    )


def _validate_config(config: AppConfig) -> None:
    if not 1 <= config.agent.max_rounds <= HARD_MAX_ROUNDS:
        raise ConfigError(
            f"agent.max_rounds must be between 1 and {HARD_MAX_ROUNDS}"
        )
    positive_values = {
        "service.startup_timeout_seconds": config.service.startup_timeout_seconds,
        "service.request_timeout_seconds": config.service.request_timeout_seconds,
        "limits.read_bytes": config.limits.read_bytes,
        "limits.write_bytes": config.limits.write_bytes,
        "limits.output_chars": config.limits.output_chars,
        "limits.command_chars": config.limits.command_chars,
        "powershell.timeout_seconds": config.powershell.timeout_seconds,
        "powershell.max_pipeline_stages": config.powershell.max_pipeline_stages,
    }
    for label, value in positive_values.items():
        if value <= 0:
            raise ConfigError(f"{label} must be positive")
