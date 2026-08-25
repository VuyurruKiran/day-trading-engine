from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

_EXCLUDED_NAMES = {"questrade_tokens.json", ".env"}
_TOKEN_TEMP_PREFIX = ".questrade_tokens.json."


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _safe_relative(value: str) -> Path | None:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or any(part in {"", "."} or ":" in part for part in path.parts)
    ):
        return None
    return Path(*path.parts)


def _excluded_source(path: Path) -> bool:
    return path.name in _EXCLUDED_NAMES or (
        path.name.startswith(_TOKEN_TEMP_PREFIX) and path.name.endswith(".tmp")
    )


def same_volume(left: Path, right: Path) -> bool:
    """Conservatively detect whether source and backup are on the same storage volume."""
    left = left.resolve()
    right.mkdir(parents=True, exist_ok=True)
    resolved_right = right.resolve()
    if left.drive or resolved_right.drive:
        return left.drive.casefold() == resolved_right.drive.casefold()
    return left.stat().st_dev == resolved_right.stat().st_dev


def create_backup(data_dir: Path, destination: Path) -> Path:
    """Create a checksummed research-data backup without copying OAuth secrets."""
    if not data_dir.exists():
        raise FileNotFoundError(data_dir)
    source_root = data_dir.resolve()
    destination_root = destination.resolve()
    if destination_root == source_root or destination_root.is_relative_to(source_root):
        raise ValueError("backup destination must be outside the runtime data directory")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = destination / stamp
    target.mkdir(parents=True, exist_ok=False)

    files: dict[str, str] = {}
    for source in sorted(data_dir.rglob("*")):
        if source.is_symlink():
            raise ValueError(f"backup source must not contain symbolic links: {source}")
        if not source.is_file():
            continue
        resolved_source = source.resolve()
        if not resolved_source.is_relative_to(source_root):
            raise ValueError(f"backup source escaped runtime data directory: {source}")
        if _excluded_source(source):
            continue
        relative = source.relative_to(data_dir)
        output = target / "data" / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix in {".db", ".sqlite", ".sqlite3"}:
            with (
                closing(sqlite3.connect(source)) as src,
                closing(sqlite3.connect(output)) as dst,
            ):
                src.backup(dst)
        else:
            shutil.copy2(source, output)
        files[relative.as_posix()] = _sha256(output)

    same_disk = same_volume(data_dir, destination)
    created_at = datetime.now(UTC).isoformat()
    manifest: dict[str, object] = {
        "version": 1,
        "created_at": created_at,
        "same_volume_as_source": same_disk,
        "files": files,
        "metadata_files": {},
    }
    _write_json(target / "manifest.json", manifest)
    _write_json(
        data_dir / "backup_status.json",
        {
            "created_at": created_at,
            "destination": str(target.resolve()),
            "same_volume_as_source": same_disk,
        },
    )
    return target


def create_month_end_snapshot(
    data_dir: Path,
    destination: Path,
    *,
    month: str,
    versions: dict[str, str],
) -> Path:
    """Create a month-end copy plus version/checksum metadata."""
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise ValueError("month must use YYYY-MM") from exc
    required = {"algorithm", "config", "schema"}
    if required - versions.keys():
        raise ValueError("algorithm, config and schema versions are required")
    target = create_backup(data_dir, destination / "month-end" / month)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot: dict[str, object] = {
        "month": month,
        "versions": {key: versions[key] for key in sorted(versions)},
        "files": manifest["files"],
        "created_at": manifest["created_at"],
    }
    snapshot_path = target / "research_snapshot.json"
    _write_json(snapshot_path, snapshot)
    manifest["metadata_files"] = {"research_snapshot.json": _sha256(snapshot_path)}
    _write_json(manifest_path, manifest)
    return target


def verify_backup(backup: Path) -> tuple[bool, tuple[str, ...]]:
    """Verify declared backup files and reject undeclared content."""
    payload = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    files = payload.get("files")
    metadata_files = payload.get("metadata_files", {})
    if not isinstance(files, dict) or not isinstance(metadata_files, dict):
        return False, ("manifest:invalid",)

    failures: list[str] = []
    declared: dict[Path, str] = {}
    for relative, expected in files.items():
        safe = _safe_relative(str(relative))
        if safe is None or not isinstance(expected, str):
            failures.append(f"manifest-path:{relative}")
            continue
        declared[safe] = expected
        path = backup / "data" / safe
        if not path.is_file():
            failures.append(f"missing:{relative}")
        elif _sha256(path) != expected:
            failures.append(f"checksum:{relative}")

    data_root = backup / "data"
    actual = (
        {path.relative_to(data_root) for path in data_root.rglob("*") if path.is_file()}
        if data_root.exists()
        else set()
    )
    for extra in sorted(actual - set(declared)):
        failures.append(f"undeclared:{extra.as_posix()}")

    for relative, expected in metadata_files.items():
        safe = _safe_relative(str(relative))
        if safe is None or not isinstance(expected, str):
            failures.append(f"metadata-path:{relative}")
            continue
        path = backup / safe
        if not path.is_file():
            failures.append(f"missing-metadata:{relative}")
        elif _sha256(path) != expected:
            failures.append(f"checksum-metadata:{relative}")
    return not failures, tuple(failures)


def restore_backup(backup: Path, destination: Path, *, verify_only: bool = False) -> Path:
    """Verify a backup and optionally restore its manifest-declared data."""
    valid, failures = verify_backup(backup)
    if not valid:
        raise ValueError(f"backup verification failed: {', '.join(failures)}")
    if verify_only:
        return destination
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("restore destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    payload = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    files = payload["files"]
    for relative in sorted(files):
        safe = _safe_relative(str(relative))
        if safe is None:
            raise ValueError(f"unsafe manifest path: {relative}")
        source = backup / "data" / safe
        output = destination / safe
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)
    return destination
