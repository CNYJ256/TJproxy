from pathlib import Path

import pytest

from tjproxy_agent.policy import (
    ApprovalLifetime,
    PolicyConfigError,
    Risk,
    load_policy_config,
)


def test_missing_policy_file_uses_builtin_dev_and_plan_profiles(tmp_path: Path):
    config = load_policy_config(tmp_path / "agent.policy.toml")

    assert config.default_profile == "dev"
    assert config.approval_lifetime == ApprovalLifetime.ONCE
    assert "powershell" in config.profiles["dev"].allow_tools
    assert "powershell" in config.profiles["plan"].deny_tools
    assert config.profiles["plan"].write_allow_globs == ("docs/plan/*plan.md",)
    assert Risk.VCS_DESTRUCTIVE in config.profiles["dev"].approval_required_risks


def test_policy_file_overrides_default_profile_and_commands(tmp_path: Path):
    path = tmp_path / "agent.policy.toml"
    path.write_text(
        """
[policy]
default_profile = "dev"
approval_lifetime = "once"

[profiles.dev]
allow_tools = ["read", "powershell"]
approval_required_risks = ["vcs_destructive"]

[[commands]]
name = "git"
allow_subcommands = ["status"]
approve_subcommands = ["reset"]
deny_args = ["--upload-pack"]
""".strip(),
        encoding="utf-8",
    )

    config = load_policy_config(path)

    assert config.default_profile == "dev"
    assert config.commands["git"].allow_subcommands == ("status",)
    assert config.commands["git"].approve_subcommands == ("reset",)
    assert config.commands["git"].deny_args == ("--upload-pack",)


def test_policy_file_rejects_unknown_sections_and_unknown_risks(tmp_path: Path):
    unknown_section = tmp_path / "unknown-section.toml"
    unknown_section.write_text("[magic]\nenabled = true\n", encoding="utf-8")
    unknown_risk = tmp_path / "unknown-risk.toml"
    unknown_risk.write_text(
        """
[policy]
default_profile = "dev"
approval_lifetime = "once"

[profiles.dev]
allow_tools = ["powershell"]
approval_required_risks = ["surprise"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(PolicyConfigError, match="unknown section"):
        load_policy_config(unknown_section)
    with pytest.raises(PolicyConfigError, match="unknown risk"):
        load_policy_config(unknown_risk)


from tjproxy_agent.policy import PolicyContext, PolicyDecision, PolicyEngine
from tjproxy_agent.protocol import ToolCall


def test_policy_allows_readonly_tools_and_safe_git_status(tmp_path: Path):
    engine = PolicyEngine(load_policy_config(tmp_path / "missing.toml"))
    context = PolicyContext(profile="dev", task_id="task-1", workspace=tmp_path)

    read_decision = engine.review(ToolCall("read", {"path": "README.md"}), context)
    git_decision = engine.review(
        ToolCall(
            "powershell",
            {"pipeline": [{"command": "git", "args": ["status", "--short"]}]},
        ),
        context,
    )

    assert read_decision.kind == PolicyDecision.ALLOW
    assert git_decision.kind == PolicyDecision.ALLOW


def test_policy_requires_approval_for_git_reset_and_npm_install(tmp_path: Path):
    engine = PolicyEngine(load_policy_config(tmp_path / "missing.toml"))
    context = PolicyContext(profile="dev", task_id="task-1", workspace=tmp_path)

    reset_decision = engine.review(
        ToolCall(
            "powershell",
            {"pipeline": [{"command": "git", "args": ["reset", "--hard"]}]},
        ),
        context,
    )
    install_decision = engine.review(
        ToolCall(
            "powershell",
            {"pipeline": [{"command": "npm", "args": ["install"]}]},
        ),
        context,
    )

    assert reset_decision.kind == PolicyDecision.APPROVAL_REQUIRED
    assert reset_decision.risk == Risk.VCS_DESTRUCTIVE
    assert "git reset" in reset_decision.summary
    assert install_decision.kind == PolicyDecision.APPROVAL_REQUIRED
    assert install_decision.risk == Risk.DEPENDENCY_INSTALL


def test_policy_denies_unknown_command_and_plan_mode_powershell(tmp_path: Path):
    engine = PolicyEngine(load_policy_config(tmp_path / "missing.toml"))
    dev = PolicyContext(profile="dev", task_id="task-1", workspace=tmp_path)
    plan = PolicyContext(profile="plan", task_id="task-1", workspace=tmp_path)

    unknown = engine.review(
        ToolCall(
            "powershell",
            {"pipeline": [{"command": "Remove-Item", "args": ["x.txt"]}]},
        ),
        dev,
    )
    plan_shell = engine.review(
        ToolCall(
            "powershell",
            {"pipeline": [{"command": "git", "args": ["status"]}]},
        ),
        plan,
    )

    assert unknown.kind == PolicyDecision.DENY
    assert unknown.error_code == "POLICY_DENIED"
    assert plan_shell.kind == PolicyDecision.DENY
    assert "profile" in plan_shell.reason


def test_plan_mode_allows_only_docs_plan_writes(tmp_path: Path):
    engine = PolicyEngine(load_policy_config(tmp_path / "missing.toml"))
    context = PolicyContext(profile="plan", task_id="task-1", workspace=tmp_path)

    allowed = engine.review(
        ToolCall("write", {"path": "docs/plan/agent-plan.md", "content": "plan"}),
        context,
    )
    blocked = engine.review(
        ToolCall("write", {"path": "src/app.py", "content": "code"}),
        context,
    )

    assert allowed.kind == PolicyDecision.ALLOW
    assert blocked.kind == PolicyDecision.DENY


from tjproxy_agent.policy import ApprovalStore


def test_approval_store_consumes_exact_call_once(tmp_path: Path):
    context = PolicyContext(profile="dev", task_id="task-1", workspace=tmp_path)
    call = ToolCall(
        "powershell",
        {"pipeline": [{"command": "git", "args": ["reset", "--hard"]}]},
    )
    engine = PolicyEngine(load_policy_config(tmp_path / "missing.toml"))
    review = engine.review(call, context)
    store = ApprovalStore()

    approval = store.create(review, call, context)

    assert store.consume(approval.approval_id, call, context) is True
    assert store.consume(approval.approval_id, call, context) is False


def test_approval_store_rejects_changed_arguments_or_task(tmp_path: Path):
    context = PolicyContext(profile="dev", task_id="task-1", workspace=tmp_path)
    changed_task = PolicyContext(profile="dev", task_id="task-2", workspace=tmp_path)
    call = ToolCall(
        "powershell",
        {"pipeline": [{"command": "git", "args": ["reset", "--hard"]}]},
    )
    changed_call = ToolCall(
        "powershell",
        {"pipeline": [{"command": "git", "args": ["reset", "--soft"]}]},
    )
    engine = PolicyEngine(load_policy_config(tmp_path / "missing.toml"))
    review = engine.review(call, context)
    store = ApprovalStore()
    approval = store.create(review, call, context)

    assert store.consume(approval.approval_id, changed_call, context) is False
    assert store.consume(approval.approval_id, call, changed_task) is False
