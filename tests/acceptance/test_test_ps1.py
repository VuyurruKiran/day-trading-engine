import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(os.name != "nt", reason="PowerShell wrapper is Windows-specific")


def _run_test_script(
    tmp_path: Path,
    ruff_exit: int,
    pytest_exit: int,
    coverage_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Run test.ps1 with a fake uv command that returns controlled exit codes."""
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")

    fake_uv = tmp_path / "uv.cmd"
    fake_uv.write_text(
        "@echo off\n"
        'echo %* | findstr /C:"ruff check" >nul\n'
        "if not errorlevel 1 exit /b %FAKE_RUFF_EXIT%\n"
        'echo %* | findstr /C:"pytest" >nul\n'
        "if errorlevel 1 exit /b 0\n"
        'if not "%FAKE_PYTEST_EXIT%"=="0" exit /b %FAKE_PYTEST_EXIT%\n'
        "exit /b %FAKE_COVERAGE_EXIT%\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    env["FAKE_RUFF_EXIT"] = str(ruff_exit)
    env["FAKE_PYTEST_EXIT"] = str(pytest_exit)
    env["FAKE_COVERAGE_EXIT"] = str(coverage_exit)

    return subprocess.run(
        [shell, "-NoProfile", "-File", str(ROOT / "test.ps1")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_test_ps1_propagates_ruff_failure(tmp_path: Path) -> None:
    """A ruff failure must stop the wrapper and return its non-zero code."""
    result = _run_test_script(tmp_path, ruff_exit=7, pytest_exit=0)
    assert result.returncode == 7


def test_test_ps1_propagates_pytest_failure(tmp_path: Path) -> None:
    """A pytest failure must propagate through the PowerShell wrapper."""
    result = _run_test_script(tmp_path, ruff_exit=0, pytest_exit=8)
    assert result.returncode == 8


def test_test_ps1_propagates_coverage_failure(tmp_path: Path) -> None:
    """A below-threshold pytest-cov result must fail the wrapper."""
    result = _run_test_script(tmp_path, ruff_exit=0, pytest_exit=0, coverage_exit=1)
    assert result.returncode == 1
