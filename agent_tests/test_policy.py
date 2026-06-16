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
