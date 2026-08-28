from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path

from day_trading_engine.engine.domain import DecisionStatus


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _utc_iso(value: datetime) -> str:
    _require_aware(value, "timestamp")
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class SavedReport:
    snapshot_id: str
    created_at: datetime
    primary_symbol: str | None
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id is required")
        _require_aware(self.created_at, "created_at")


class ReportStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    snapshot_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    primary_symbol TEXT,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL,
                    at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES reports(snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS execution_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('entry', 'exit')),
                    at TEXT NOT NULL,
                    price REAL NOT NULL CHECK(price > 0),
                    FOREIGN KEY(snapshot_id) REFERENCES reports(snapshot_id)
                );
                """
            )

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def save_once(self, report: SavedReport) -> SavedReport:
        """Persist at most one completed decision report per trading session."""
        if report.payload.get("decision_state") == "DATA_NOT_READY":
            return report

        session = report.payload.get("session")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if isinstance(session, str):
                rows = db.execute(
                    "SELECT snapshot_id, created_at, primary_symbol, payload FROM reports"
                ).fetchall()
                for row in rows:
                    payload = json.loads(row[3])
                    if payload.get("session") == session:
                        return SavedReport(
                            row[0], datetime.fromisoformat(row[1]), row[2], payload
                        )
            db.execute(
                "INSERT INTO reports VALUES (?, ?, ?, ?)",
                (
                    report.snapshot_id,
                    _utc_iso(report.created_at),
                    report.primary_symbol,
                    json.dumps(report.payload, sort_keys=True, separators=(",", ":")),
                ),
            )
        return report

    def append_transition(
        self, snapshot_id: str, *, at: datetime, status: DecisionStatus, reason: str
    ) -> None:
        _require_aware(at, "at")
        if not reason.strip():
            raise ValueError("status transition reason is required")
        with self._db() as db:
            db.execute(
                "INSERT INTO transitions(snapshot_id, at, status, reason) VALUES (?, ?, ?, ?)",
                (snapshot_id, _utc_iso(at), status.value, reason),
            )

    def record_execution(
        self, snapshot_id: str, *, kind: str, at: datetime, price: float
    ) -> None:
        _require_aware(at, "at")
        if kind not in {"entry", "exit"}:
            raise ValueError("execution kind must be entry or exit")
        if not isfinite(price) or price <= 0:
            raise ValueError("execution price must be finite and positive")
        with self._db() as db:
            db.execute(
                "INSERT INTO execution_events(snapshot_id, kind, at, price) VALUES (?, ?, ?, ?)",
                (snapshot_id, kind, _utc_iso(at), price),
            )

    def load(self, snapshot_id: str) -> SavedReport:
        with self._db() as db:
            row = db.execute(
                "SELECT snapshot_id, created_at, primary_symbol, payload "
                "FROM reports WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        return SavedReport(row[0], datetime.fromisoformat(row[1]), row[2], json.loads(row[3]))

    def latest(self) -> SavedReport | None:
        with self._db() as db:
            row = db.execute(
                "SELECT snapshot_id, created_at, primary_symbol, payload "
                "FROM reports ORDER BY julianday(created_at) DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return SavedReport(row[0], datetime.fromisoformat(row[1]), row[2], json.loads(row[3]))

    def has_open_execution(self) -> bool:
        with self._db() as db:
            row = db.execute(
                "SELECT kind FROM execution_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row is not None and row[0] == "entry"

    def transitions(self, snapshot_id: str) -> tuple[tuple[str, str, str], ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT at, status, reason FROM transitions "
                "WHERE snapshot_id = ? ORDER BY id",
                (snapshot_id,),
            ).fetchall()
        return tuple((row[0], row[1], row[2]) for row in rows)

    def execution_events(self, snapshot_id: str) -> tuple[tuple[str, str, float], ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT kind, at, price FROM execution_events "
                "WHERE snapshot_id = ? ORDER BY id",
                (snapshot_id,),
            ).fetchall()
        return tuple((row[0], row[1], row[2]) for row in rows)
