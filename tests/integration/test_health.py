from pathlib import Path

from day_trading_engine.core.health import run_health_check

ROOT = Path(__file__).resolve().parents[2]


def test_health_check_passes() -> None:
    report, config = run_health_check(ROOT / "configs" / "v1.yaml")
    assert report.ok
    assert report.sqlite_ok
    assert report.duckdb_ok
    assert config.project.plan_version == "2.2"
