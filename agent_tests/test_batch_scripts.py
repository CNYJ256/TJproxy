from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_start_bat_bootstrap_contract():
    text = _script("start.bat")

    assert "where pwsh.exe" in text
    assert "learn.microsoft.com/powershell" in text
    assert "python.org/downloads/windows" in text
    assert "server\\requirements.txt" in text
    assert "-m venv .venv" in text
    assert "pip install -r server\\requirements.txt" in text
    assert "agent_cli.py" in text
    assert "--workspace" in text


def test_doctor_bat_checks_runtime_components():
    text = _script("doctor.bat")

    assert "where pwsh.exe" in text
    assert "server\\requirements.txt" in text
    assert "agent_cli.py" in text
    assert "agent.toml" in text
    assert "agent.policy.toml" in text
    assert "extension\\manifest.json" in text
    assert "import websockets, requests, textual, pytest, pytest_asyncio" in text
    assert "http://localhost:8765" in text
