from pathlib import Path
import time

import pytest

from tjproxy_agent.config import CommandPolicy, PowerShellConfig
from tjproxy_agent.powershell import PowerShellExecutor, ShellFailure
from tjproxy_agent.workspace import Workspace


def make_executor(
    tmp_path: Path, *, timeout: float = 5, output_chars: int = 2000
) -> PowerShellExecutor:
    workspace = Workspace(tmp_path, read_limit=1000, write_limit=1000)
    config = PowerShellConfig(
        executable="pwsh",
        timeout_seconds=timeout,
        max_pipeline_stages=3,
        commands=(
            CommandPolicy("git", ("status", "diff")),
            CommandPolicy("rg", denied_args=("-L", "--follow")),
            CommandPolicy("Select-String"),
            CommandPolicy("Write-Output"),
            CommandPolicy("pwsh", ("-File",), script_runner=True),
            CommandPolicy("python", ("-m",), script_runner=True),
            CommandPolicy("node", script_runner=True),
        ),
    )
    return PowerShellExecutor(
        workspace, config, command_chars=1000, output_chars=output_chars
    )


def test_allows_structured_text_pipeline(tmp_path: Path):
    result = make_executor(tmp_path).run(
        [
            {"command": "Write-Output", "args": ["hello"]},
            {"command": "Select-String", "args": ["-Pattern", "hello"]},
        ]
    )

    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert result.timed_out is False


def test_approved_unknown_command_can_run_once(tmp_path: Path):
    workspace = Workspace(tmp_path, read_limit=1000, write_limit=1000)
    executor = PowerShellExecutor(
        workspace,
        PowerShellConfig(
            executable="pwsh",
            timeout_seconds=5,
            max_pipeline_stages=3,
            commands=(CommandPolicy("Write-Output"),),
        ),
        command_chars=1000,
        output_chars=2000,
    )
    pipeline = [{"command": "python", "args": ["-c", "print('approved')"]}]

    with pytest.raises(ShellFailure, match="COMMAND_NOT_ALLOWED"):
        executor.run(pipeline)

    executor.approve_pipeline_once(pipeline)
    result = executor.run(pipeline)

    assert result.exit_code == 0
    assert "approved" in result.stdout
    with pytest.raises(ShellFailure, match="COMMAND_NOT_ALLOWED"):
        executor.run(pipeline)


@pytest.mark.parametrize(
    "stage",
    [
        {"command": "Remove-Item", "args": ["README.md"]},
        {"command": "git", "args": ["reset", "--hard"]},
        {"command": "git;Remove-Item", "args": []},
        {"command": "git", "args": ["status", ">", "out.txt"]},
        {"command": "git", "args": ["status", "$(Get-ChildItem)"]},
        {"command": "git", "args": ["status", "$env:HOME"]},
        {"command": "rg", "args": ["--follow", "needle"]},
        {"command": "rg", "args": ["needle", "../outside"]},
        {"command": "rg", "args": ["needle", "--glob=../outside"]},
        {"command": "python", "args": ["-m", "os"]},
        {"command": "pwsh", "args": ["-File", "../outside.ps1"]},
        {"command": "node", "args": ["C:/outside.js"]},
    ],
)
def test_rejects_disallowed_commands_arguments_or_shell_syntax(
    tmp_path: Path, stage
):
    with pytest.raises(ShellFailure):
        make_executor(tmp_path).run([stage])


def test_workspace_script_path_is_checked_and_executed(tmp_path: Path):
    script = tmp_path / "ok.ps1"
    script.write_text("Write-Output ok", encoding="utf-8")

    result = make_executor(tmp_path).run(
        [{"command": "pwsh", "args": ["-File", "ok.ps1"]}]
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"


def test_run_passes_explicit_stdin_to_process(tmp_path: Path):
    script = tmp_path / "echo_stdin.ps1"
    script.write_text(
        "$inputText = [Console]::In.ReadToEnd(); Write-Output \"stdin=$inputText\"",
        encoding="utf-8",
    )

    result = make_executor(tmp_path).run(
        [{"command": "pwsh", "args": ["-File", "echo_stdin.ps1"]}],
        stdin="3\n80 90 100\n",
    )

    assert result.exit_code == 0
    assert "stdin=3" in result.stdout
    assert "80 90 100" in result.stdout


def test_timeout_is_reported_and_process_tree_is_stopped(tmp_path: Path):
    script = tmp_path / "slow.ps1"
    script.write_text("Start-Sleep -Seconds 5", encoding="utf-8")
    started = time.monotonic()

    result = make_executor(tmp_path, timeout=0.2).run(
        [{"command": "pwsh", "args": ["-File", "slow.ps1"]}]
    )

    assert time.monotonic() - started < 2
    assert result.timed_out is True
    assert result.error_code == "COMMAND_TIMEOUT"
    assert result.exit_code is None


def test_output_is_truncated_and_marked(tmp_path: Path):
    result = make_executor(tmp_path, output_chars=5).run(
        [{"command": "Write-Output", "args": ["123456789"]}]
    )

    assert result.stdout == "12345"
    assert result.truncated is True


def test_pipeline_stage_limit_is_enforced(tmp_path: Path):
    stages = [{"command": "Write-Output", "args": ["x"]}] * 4

    with pytest.raises(ShellFailure, match="PIPELINE_LIMIT"):
        make_executor(tmp_path).run(stages)
