from pathlib import Path

import pytest

from tjproxy_agent.config import HARD_MAX_ROUNDS, ConfigError, load_config


def test_missing_config_uses_conservative_defaults(tmp_path: Path):
    config = load_config(tmp_path / "missing.toml")

    assert config.agent.max_rounds == 32
    assert config.agent.prompt_path is None
    assert config.service.base_url == "http://localhost:8765"
    assert config.powershell.timeout_seconds == 60
    assert config.limits.output_chars == 20_000


def test_toml_overrides_rounds_and_timeout(tmp_path: Path):
    path = tmp_path / "agent.toml"
    path.write_text(
        "[agent]\nmax_rounds = 12\nprompt_path = 'prompt.txt'\n"
        "[powershell]\ntimeout_seconds = 15\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.agent.max_rounds == 12
    assert config.agent.prompt_path == "prompt.txt"
    assert config.powershell.timeout_seconds == 15


def test_unknown_key_is_rejected(tmp_path: Path):
    path = tmp_path / "agent.toml"
    path.write_text("[agent]\nmax_rounds = 32\nmagic = true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown key: agent.magic"):
        load_config(path)


def test_round_limit_above_hard_max_is_rejected(tmp_path: Path):
    path = tmp_path / "agent.toml"
    path.write_text(
        f"[agent]\nmax_rounds = {HARD_MAX_ROUNDS + 1}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="max_rounds"):
        load_config(path)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[agent]\nmax_rounds = true\n", "agent.max_rounds"),
        ("[service]\nbase_url = 3\n", "service.base_url"),
        ("[powershell]\ntimeout_seconds = 0\n", "timeout_seconds"),
        ("[limits]\nread_bytes = -1\n", "read_bytes"),
        (
            "[[powershell.commands]]\nname = 'git'\nallow_in_pipeline = 'yes'\n",
            "allow_in_pipeline",
        ),
    ],
)
def test_invalid_config_types_and_ranges_are_rejected(
    tmp_path: Path, text: str, message: str
):
    path = tmp_path / "agent.toml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_duplicate_command_names_are_rejected_case_insensitively(tmp_path: Path):
    path = tmp_path / "agent.toml"
    path.write_text(
        "[[powershell.commands]]\nname = 'git'\n"
        "[[powershell.commands]]\nname = 'GIT'\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="duplicate command policy"):
        load_config(path)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://localhost:8765",
        "http://example.com:8765",
        "http://user:pass@localhost:8765",
        "http://localhost:8765/api",
        "http://localhost:8765?x=1",
    ],
)
def test_service_url_must_be_plain_local_http_origin(tmp_path: Path, base_url: str):
    path = tmp_path / "agent.toml"
    path.write_text(
        f"[service]\nbase_url = {base_url!r}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="local HTTP origin"):
        load_config(path)


def test_runtime_config_keeps_powershell_commands_for_compatibility(tmp_path: Path):
    path = tmp_path / "agent.toml"
    path.write_text(
        "[[powershell.commands]]\nname = 'git'\nallowed_subcommands = ['status']\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.powershell.commands[0].name == "git"
