from pathlib import Path

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
    assert config.project.plan_version == "2.2"


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
