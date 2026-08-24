from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def ensure_runtime_dirs(root: Path | None = None) -> tuple[Path, Path]:
    base = root or project_root()
    data = base / "data"
    logs = base / "logs"
    data.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    return data, logs
