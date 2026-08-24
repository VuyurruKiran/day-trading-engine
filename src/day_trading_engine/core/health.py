from __future__ import annotations

import platform
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

from day_trading_engine.core.config import AppConfig, load_config
from day_trading_engine.core.paths import ensure_runtime_dirs, project_root


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    python: str
    platform: str
    config_valid: bool
    sqlite_ok: bool
    duckdb_ok: bool
    writable_data_dir: bool
    writable_logs_dir: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _is_writable(directory: Path) -> bool:
    probe = directory / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def run_health_check(config_path: Path | None = None) -> tuple[HealthReport, AppConfig]:
    root = project_root()
    config = load_config(config_path or root / "configs" / "v1.yaml")
    data_dir, logs_dir = ensure_runtime_dirs(root)

    sqlite_ok = False
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("select 1").fetchone()
        conn.close()
        sqlite_ok = True
    except sqlite3.Error:
        pass

    duckdb_ok = False
    try:
        conn = duckdb.connect(":memory:")
        conn.execute("select 1").fetchone()
        conn.close()
        duckdb_ok = True
    except Exception:
        pass

    data_writable = _is_writable(data_dir)
    logs_writable = _is_writable(logs_dir)
    report = HealthReport(
        ok=sqlite_ok and duckdb_ok and data_writable and logs_writable,
        python=sys.version.split()[0],
        platform=platform.platform(),
        config_valid=True,
        sqlite_ok=sqlite_ok,
        duckdb_ok=duckdb_ok,
        writable_data_dir=data_writable,
        writable_logs_dir=logs_writable,
    )
    return report, config
