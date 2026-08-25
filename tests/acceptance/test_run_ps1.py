from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_run_ps1_uses_python_module_launcher() -> None:
    """Avoid the Streamlit executable, which Windows Application Control may block."""
    script = (ROOT / "run.ps1").read_text(encoding="utf-8")

    assert "uv run python -m streamlit run" in script
    assert "uv run streamlit run" not in script
    assert "$LASTEXITCODE" in script
