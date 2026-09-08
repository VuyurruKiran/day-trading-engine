import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_run_ps1_uses_python_module_launchers() -> None:
    """Launch both Python modules through uv and supervise both processes."""
    script = (ROOT / "run.ps1").read_text(encoding="utf-8")

    assert '"day_trading_engine.engine.live"' in script
    assert '"day_trading_engine.ui.server"' in script
    assert '"--stop-after-extended-close"' in script
    assert '.venv\\Scripts\\python.exe' in script
    assert "streamlit" not in script
    assert "Start-Process" in script
    assert "$engine.HasExited" in script
    assert "$ui.HasExited" in script
    assert "$exitCode = 1" in script
    assert "catch {" in script
    assert "engine-ui.lock" in script
    assert "[System.IO.FileShare]::None" in script
    assert "already running" in script
    assert "Stop-EngineProcessTree" in script


@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_powershell_launcher_stop_flag_and_child_exit(tmp_path, scheduled, exit_code):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")
    shutil.copy(ROOT / "run.ps1", tmp_path / "run.ps1")
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    wrapper = tmp_path / "test-launch.ps1"
    wrapper.write_text(
        'function Start-Process {\n'
        '    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)\n'
        '    Write-Output ($ArgumentList -join " ") | Out-Host\n'
        f'    [pscustomobject]@{{ HasExited = $true; ExitCode = {exit_code}; Id = 999999 }}\n'
        '}\n'
        '& "$PSScriptRoot/run.ps1"' + (' -StopAfterExtendedClose' if scheduled else '')
        + '\nexit $LASTEXITCODE\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(wrapper)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert ("--stop-after-extended-close" in result.stdout) == scheduled
