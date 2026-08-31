from pathlib import Path

import yaml

from day_trading_engine.core.health import run_health_check

ROOT = Path(__file__).resolve().parents[2]


def test_health_check_passes() -> None:
    """Healthy configuration should report all core checks as available."""
    report, config = run_health_check(ROOT / "configs" / "v1.yaml")
    assert report.ok
    assert report.config_valid
    assert report.config_error is None
    assert report.sqlite_ok
    assert report.duckdb_ok
    assert config is not None
    assert config.project.plan_version == "3.1"


def test_health_check_reports_invalid_config_without_crashing(tmp_path: Path) -> None:
    """Schema-invalid configuration should return a degraded report."""
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("project: {}\n", encoding="utf-8")

    report, config = run_health_check(invalid)

    assert not report.ok
    assert not report.config_valid
    assert report.config_error
    assert config is None


def test_health_check_reports_malformed_yaml_without_crashing(tmp_path: Path) -> None:
    """Malformed YAML should return a degraded report instead of raising."""
    invalid = tmp_path / "malformed.yaml"
    invalid.write_text("project: [\n", encoding="utf-8")

    report, config = run_health_check(invalid)

    assert not report.ok
    assert not report.config_valid
    assert report.config_error
    assert config is None


def test_health_check_reports_unknown_timezone_without_crashing(tmp_path: Path) -> None:
    """Unknown timezone configuration should degrade instead of raising."""
    data = yaml.safe_load((ROOT / "configs" / "v1.yaml").read_text(encoding="utf-8"))
    data["project"]["timezone"] = "Definitely/Not_A_Timezone"
    invalid = tmp_path / "timezone.yaml"
    invalid.write_text(yaml.safe_dump(data), encoding="utf-8")

    report, config = run_health_check(invalid)

    assert not report.ok
    assert not report.config_valid
    assert "unknown timezone" in (report.config_error or "")
    assert config is None


def test_health_check_degrades_when_runtime_dirs_cannot_be_created(monkeypatch) -> None:
    """Runtime-directory creation failures should be reported, not raised."""
    def fail_runtime_dirs(_root: Path) -> tuple[Path, Path]:
        raise OSError("read-only project root")

    monkeypatch.setattr(
        "day_trading_engine.core.health.ensure_runtime_dirs",
        fail_runtime_dirs,
    )

    report, config = run_health_check(ROOT / "configs" / "v1.yaml")

    assert not report.ok
    assert report.config_valid
    assert config is not None
    assert not report.writable_data_dir
    assert not report.writable_logs_dir
