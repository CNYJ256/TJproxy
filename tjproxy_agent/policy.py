from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
import tomllib


class PolicyConfigError(ValueError):
    """Raised when agent.policy.toml is invalid."""


class ApprovalLifetime(StrEnum):
    ONCE = "once"


class Risk(StrEnum):
    FILESYSTEM_DESTRUCTIVE = "filesystem_destructive"
    VCS_DESTRUCTIVE = "vcs_destructive"
    DEPENDENCY_INSTALL = "dependency_install"
    NETWORK = "network"
    SERVICE_START = "service_start"
    LONG_RUNNING = "long_running"
    SECRET_TOUCH = "secret_touch"


@dataclass(frozen=True)
class ProfilePolicy:
    allow_tools: tuple[str, ...] = ()
    deny_tools: tuple[str, ...] = ()
    write_allow_globs: tuple[str, ...] = ()
    approval_required_risks: tuple[Risk, ...] = ()


@dataclass(frozen=True)
class CommandRule:
    name: str
    allow_subcommands: tuple[str, ...] = ()
    approve_subcommands: tuple[str, ...] = ()
    deny_args: tuple[str, ...] = ()
    allow_modules: tuple[str, ...] = ()
    approve_modules: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectDiscoveryPolicy:
    enabled: bool = True
    read_files: tuple[str, ...] = (
        "package.json",
        "pyproject.toml",
        "pytest.ini",
        "tox.ini",
    )


@dataclass(frozen=True)
class PolicyConfig:
    default_profile: str = "dev"
    approval_lifetime: ApprovalLifetime = ApprovalLifetime.ONCE
    profiles: dict[str, ProfilePolicy] = field(default_factory=dict)
    commands: dict[str, CommandRule] = field(default_factory=dict)
    project_discovery: ProjectDiscoveryPolicy = field(
        default_factory=ProjectDiscoveryPolicy
    )


def load_policy_config(path: Path) -> PolicyConfig:
    if not path.exists():
        return default_policy_config()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PolicyConfigError(f"cannot read policy: {exc}") from exc
    return _build_policy(raw)


def default_policy_config() -> PolicyConfig:
    profiles = {
        "dev": ProfilePolicy(
            allow_tools=(
                "read",
                "list_dir",
                "read_range",
                "search",
                "project_map",
                "context_pack",
                "write",
                "edit",
                "powershell",
            ),
            approval_required_risks=tuple(Risk),
        ),
        "plan": ProfilePolicy(
            allow_tools=(
                "read",
                "list_dir",
                "read_range",
                "search",
                "project_map",
                "context_pack",
                "write",
                "edit",
            ),
            deny_tools=("powershell",),
            write_allow_globs=("docs/plan/*plan.md",),
            approval_required_risks=tuple(Risk),
        ),
    }
    commands = {
        "git": CommandRule(
            "git",
            allow_subcommands=(
                "status",
                "diff",
                "log",
                "show",
                "rev-parse",
                "ls-files",
                "grep",
            ),
            approve_subcommands=(
                "reset",
                "checkout",
                "clean",
                "rebase",
                "push",
                "remote",
                "tag",
                "branch",
            ),
        ),
        "rg": CommandRule("rg", deny_args=("-L", "--follow")),
        "python": CommandRule(
            "python",
            allow_subcommands=("-m",),
            allow_modules=("pytest", "unittest", "compileall"),
            approve_modules=("pip",),
        ),
        "npm": CommandRule(
            "npm",
            allow_subcommands=("test", "run"),
            approve_subcommands=("install", "ci", "add", "update"),
        ),
    }
    return PolicyConfig(profiles=profiles, commands=commands)


def _build_policy(raw: dict[str, Any]) -> PolicyConfig:
    allowed_sections = {"policy", "profiles", "commands", "project_discovery"}
    unknown_sections = set(raw) - allowed_sections
    if unknown_sections:
        raise PolicyConfigError(f"unknown section: {sorted(unknown_sections)[0]}")

    policy = raw.get("policy", {})
    profiles_raw = raw.get("profiles", {})
    commands_raw = raw.get("commands", [])
    discovery_raw = raw.get("project_discovery", {})

    if not isinstance(policy, dict):
        raise PolicyConfigError("policy must be a table")
    if not isinstance(profiles_raw, dict):
        raise PolicyConfigError("profiles must be a table")
    if not isinstance(commands_raw, list):
        raise PolicyConfigError("commands must be an array of tables")
    if not isinstance(discovery_raw, dict):
        raise PolicyConfigError("project_discovery must be a table")

    default_profile = _string(policy.get("default_profile", "dev"), "policy.default_profile")
    lifetime = ApprovalLifetime(
        _string(policy.get("approval_lifetime", "once"), "policy.approval_lifetime")
    )
    profiles = {
        name: _build_profile(name, value) for name, value in profiles_raw.items()
    }
    commands = {
        rule.name.casefold(): rule for rule in (_build_command(item) for item in commands_raw)
    }
    discovery = ProjectDiscoveryPolicy(
        enabled=_bool(discovery_raw.get("enabled", True), "project_discovery.enabled"),
        read_files=_string_tuple(
            discovery_raw.get(
                "read_files",
                ["package.json", "pyproject.toml", "pytest.ini", "tox.ini"],
            ),
            "project_discovery.read_files",
        ),
    )
    return PolicyConfig(
        default_profile=default_profile,
        approval_lifetime=lifetime,
        profiles=profiles,
        commands=commands,
        project_discovery=discovery,
    )


def _build_profile(name: str, value: Any) -> ProfilePolicy:
    if not isinstance(value, dict):
        raise PolicyConfigError(f"profiles.{name} must be a table")
    return ProfilePolicy(
        allow_tools=_string_tuple(value.get("allow_tools", []), f"profiles.{name}.allow_tools"),
        deny_tools=_string_tuple(value.get("deny_tools", []), f"profiles.{name}.deny_tools"),
        write_allow_globs=_string_tuple(
            value.get("write_allow_globs", []),
            f"profiles.{name}.write_allow_globs",
        ),
        approval_required_risks=tuple(
            _risk(item, f"profiles.{name}.approval_required_risks")
            for item in _string_tuple(
                value.get("approval_required_risks", []),
                f"profiles.{name}.approval_required_risks",
            )
        ),
    )


def _build_command(value: Any) -> CommandRule:
    if not isinstance(value, dict):
        raise PolicyConfigError("commands entries must be tables")
    name = _string(value.get("name"), "commands.name")
    return CommandRule(
        name=name,
        allow_subcommands=_string_tuple(value.get("allow_subcommands", []), f"commands.{name}.allow_subcommands"),
        approve_subcommands=_string_tuple(value.get("approve_subcommands", []), f"commands.{name}.approve_subcommands"),
        deny_args=_string_tuple(value.get("deny_args", []), f"commands.{name}.deny_args"),
        allow_modules=_string_tuple(value.get("allow_modules", []), f"commands.{name}.allow_modules"),
        approve_modules=_string_tuple(value.get("approve_modules", []), f"commands.{name}.approve_modules"),
    )


def _risk(value: str, label: str) -> Risk:
    try:
        return Risk(value)
    except ValueError as exc:
        raise PolicyConfigError(f"unknown risk in {label}: {value}") from exc


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyConfigError(f"{label} must be a non-empty string")
    return value


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyConfigError(f"{label} must be a boolean")
    return value


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PolicyConfigError(f"{label} must be an array of non-empty strings")
    return tuple(value)
