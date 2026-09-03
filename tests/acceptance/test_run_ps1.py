from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_run_ps1_uses_python_module_launchers() -> None:
    """Launch both Python modules through uv and supervise both processes."""
    script = (ROOT / "run.ps1").read_text(encoding="utf-8")

    assert '"day_trading_engine.engine.live"' in script
    assert '"day_trading_engine.ui.server"' in script
    assert "streamlit" not in script
    assert "Start-Process" in script
    assert "$engine.HasExited" in script
    assert "$ui.HasExited" in script
    assert "$exitCode = 1" in script
    assert "catch {" in script
    assert "engine-ui.lock" in script
    assert "[System.IO.FileShare]::None" in script
    assert "already running" in script
