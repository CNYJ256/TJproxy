from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
from typing import Any
import tomllib
from uuid import uuid4

from .protocol import ToolCall


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


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(frozen=True)
class PolicyContext:
    profile: str
    task_id: str
    workspace: Path


@dataclass(frozen=True)
class ReviewResult:
    kind: PolicyDecision
    reason: str = ""
    risk: Risk | None = None
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    call_hash: str = ""


class PolicyEngine:
    def __init__(self, config: PolicyConfig):
        self.config = config

    def review(self, call: ToolCall, context: PolicyContext) -> ReviewResult:
        profile = self.config.profiles.get(context.profile)
        if profile is None:
            return ReviewResult(
                PolicyDecision.DENY,
                reason=f"unknown profile: {context.profile}",
                error_code="POLICY_DENIED",
                call_hash=_call_hash(call, context),
            )
        if call.tool in profile.deny_tools:
            return ReviewResult(
                PolicyDecision.DENY,
                reason=f"tool denied by profile: {call.tool}",
                error_code="POLICY_DENIED",
                call_hash=_call_hash(call, context),
            )
        if call.tool not in profile.allow_tools:
            return ReviewResult(
                PolicyDecision.DENY,
                reason=f"tool is not allowed by profile: {call.tool}",
                error_code="POLICY_DENIED",
                call_hash=_call_hash(call, context),
            )
        if call.tool in {"write", "edit"}:
            return self._review_write_like(call, context, profile)
        if call.tool != "powershell":
            return ReviewResult(PolicyDecision.ALLOW, call_hash=_call_hash(call, context))
        return self._review_powershell(call, context, profile)

    def _review_write_like(
        self, call: ToolCall, context: PolicyContext, profile: ProfilePolicy
    ) -> ReviewResult:
        raw_path = call.arguments["path"]
        if profile.write_allow_globs and not any(
            fnmatchcase(raw_path.replace("\\", "/"), pattern)
            for pattern in profile.write_allow_globs
        ):
            return ReviewResult(
                PolicyDecision.DENY,
                reason=f"write path is not allowed in profile {context.profile}: {raw_path}",
                error_code="POLICY_DENIED",
                call_hash=_call_hash(call, context),
            )
        if _looks_sensitive_path(raw_path):
            return ReviewResult(
                PolicyDecision.APPROVAL_REQUIRED,
                reason=f"sensitive file path: {raw_path}",
                risk=Risk.SECRET_TOUCH,
                summary=f"{call.tool} {raw_path}",
                details={"path": raw_path},
                call_hash=_call_hash(call, context),
            )
        return ReviewResult(PolicyDecision.ALLOW, call_hash=_call_hash(call, context))

    def _review_powershell(
        self, call: ToolCall, context: PolicyContext, profile: ProfilePolicy
    ) -> ReviewResult:
        pipeline = call.arguments["pipeline"]
        for stage in pipeline:
            command = stage["command"]
            args = stage["args"]
            rule = self.config.commands.get(command.casefold())
            if rule is None:
                return ReviewResult(
                    PolicyDecision.DENY,
                    reason=f"command is not in policy: {command}",
                    error_code="POLICY_DENIED",
                    call_hash=_call_hash(call, context),
                )
            denied = [arg for arg in args if arg in rule.deny_args]
            if denied:
                return ReviewResult(
                    PolicyDecision.DENY,
                    reason=f"argument denied for {command}: {denied[0]}",
                    error_code="POLICY_DENIED",
                    call_hash=_call_hash(call, context),
                )
            decision = self._review_command_rule(command, args, rule, context)
            if decision.kind != PolicyDecision.ALLOW:
                return decision
        return ReviewResult(PolicyDecision.ALLOW, call_hash=_call_hash(call, context))

    def _review_command_rule(
        self, command: str, args: list[str], rule: CommandRule, context: PolicyContext
    ) -> ReviewResult:
        first = args[0] if args else ""
        call_hash = _call_hash(ToolCall("powershell", {"pipeline": [{"command": command, "args": args}]}), context)
        if command.casefold() == "python" and first == "-m":
            module = args[1] if len(args) > 1 else ""
            if module in rule.allow_modules:
                return ReviewResult(PolicyDecision.ALLOW, call_hash=call_hash)
            if module in rule.approve_modules:
                return ReviewResult(
                    PolicyDecision.APPROVAL_REQUIRED,
                    reason=f"Python module changes environment or dependencies: {module}",
                    risk=Risk.DEPENDENCY_INSTALL,
                    summary=f"python -m {module}",
                    details={"command": command, "args": args},
                    call_hash=call_hash,
                )
        if first in rule.allow_subcommands or not rule.allow_subcommands and first not in rule.approve_subcommands:
            return ReviewResult(PolicyDecision.ALLOW, call_hash=call_hash)
        if first in rule.approve_subcommands:
            risk = _risk_for_command(command, first)
            return ReviewResult(
                PolicyDecision.APPROVAL_REQUIRED,
                reason=_reason_for_command(command, first),
                risk=risk,
                summary=f"{command} {' '.join(args)}".strip(),
                details={"command": command, "args": args},
                call_hash=call_hash,
            )
        return ReviewResult(
            PolicyDecision.DENY,
            reason=f"subcommand is not allowed: {command} {first}",
            error_code="POLICY_DENIED",
            call_hash=call_hash,
        )


def _risk_for_command(command: str, subcommand: str) -> Risk:
    if command.casefold() == "git":
        return Risk.VCS_DESTRUCTIVE
    if command.casefold() in {"npm", "python"}:
        return Risk.DEPENDENCY_INSTALL
    return Risk.LONG_RUNNING


def _reason_for_command(command: str, subcommand: str) -> str:
    if command.casefold() == "git":
        return f"version-control operation may rewrite or publish repository state: git {subcommand}"
    if command.casefold() == "npm":
        return f"dependency or environment operation requires approval: npm {subcommand}"
    return f"command requires approval: {command} {subcommand}"


def _looks_sensitive_path(raw_path: str) -> bool:
    lowered = raw_path.replace("\\", "/").casefold()
    names = lowered.split("/")
    return any(
        name.startswith(".env")
        or "secret" in name
        or "token" in name
        or "credential" in name
        or name.endswith((".pem", ".key", ".pfx"))
        for name in names
    )


def _call_hash(call: ToolCall, context: PolicyContext) -> str:
    payload = {
        "tool": call.tool,
        "arguments": call.arguments,
        "task_id": context.task_id,
        "workspace": str(context.workspace.resolve()),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    call_hash: str
    task_id: str
    summary: str
    reason: str
    risk: Risk | None
    details: dict[str, Any]


class ApprovalStore:
    def __init__(self):
        self._requests: dict[str, ApprovalRequest] = {}

    def create(
        self, review: ReviewResult, call: ToolCall, context: PolicyContext
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            approval_id=uuid4().hex,
            call_hash=_call_hash(call, context),
            task_id=context.task_id,
            summary=review.summary,
            reason=review.reason,
            risk=review.risk,
            details=review.details,
        )
        self._requests[approval.approval_id] = approval
        return approval

    def consume(
        self, approval_id: str, call: ToolCall, context: PolicyContext
    ) -> bool:
        approval = self._requests.get(approval_id)
        if approval is None:
            return False
        expected = _call_hash(call, context)
        if approval.call_hash != expected or approval.task_id != context.task_id:
            return False
        del self._requests[approval_id]
        return True

    def clear_task(self, task_id: str) -> None:
        for approval_id, approval in list(self._requests.items()):
            if approval.task_id == task_id:
                del self._requests[approval_id]

    def clear_all(self) -> None:
        self._requests.clear()
