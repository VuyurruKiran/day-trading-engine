from __future__ import annotations

import platform
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import yaml
from pydantic import ValidationError

from day_trading_engine.core.config import AppConfig, load_config
from day_trading_engine.core.paths import ensure_runtime_dirs, project_root


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    python: str
    platform: str
    config_valid: bool
    config_error: str | None
    sqlite_ok: bool
    duckdb_ok: bool
    writable_data_dir: bool
    writable_logs_dir: bool

    def to_dict(self) -> dict[str, object]:
        """Return a serializable health-report mapping."""
        return asdict(self)


def _is_writable(directory: Path) -> bool:
    """Return whether a runtime directory accepts a temporary write."""
    probe = directory / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def run_health_check(config_path: Path | None = None) -> tuple[HealthReport, AppConfig | None]:
    """Check configuration, embedded databases, and runtime directories."""
    root = project_root()
    runtime_dirs_ok = True
    try:
        data_dir, logs_dir = ensure_runtime_dirs(root)
    except OSError:
        runtime_dirs_ok = False
        data_dir, logs_dir = root / "data", root / "logs"

    config: AppConfig | None = None
    config_error: str | None = None
    try:
        config = load_config(config_path or root / "configs" / "v1.yaml")
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as exc:
        config_error = str(exc)

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

    data_writable = runtime_dirs_ok and _is_writable(data_dir)
    logs_writable = runtime_dirs_ok and _is_writable(logs_dir)
    config_valid = config is not None
    report = HealthReport(
        ok=config_valid and sqlite_ok and duckdb_ok and data_writable and logs_writable,
        python=sys.version.split()[0],
        platform=platform.platform(),
        config_valid=config_valid,
        config_error=config_error,
        sqlite_ok=sqlite_ok,
        duckdb_ok=duckdb_ok,
        writable_data_dir=data_writable,
        writable_logs_dir=logs_writable,
    )
    return report, config
