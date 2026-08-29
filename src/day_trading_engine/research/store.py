from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


class ResearchStore:
    """Append-only storage for immutable cohort rows and research-only outcomes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS decision_rows (
                    snapshot_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS shadow_outcomes (
                    snapshot_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, symbol)
                );
                """
            )

    def save_decision_rows(self, snapshot_id: str, rows: list[dict[str, object]]) -> None:
        if len(rows) != 30 or len({str(row.get("symbol", "")) for row in rows}) != 30:
            raise ValueError("research snapshot must contain exactly 30 unique symbols")
        encoded = [
            (snapshot_id, str(row["symbol"]), json.dumps(row, sort_keys=True, separators=(",", ":")))
            for row in rows
        ]
        with closing(sqlite3.connect(self.path)) as db, db:
            for key, symbol, payload in encoded:
                existing = db.execute(
                    "SELECT payload FROM decision_rows WHERE snapshot_id = ? AND symbol = ?",
                    (key, symbol),
                ).fetchone()
                if existing is not None and existing[0] != payload:
                    raise ValueError("immutable research decision row already exists with different data")
                db.execute(
                    "INSERT OR IGNORE INTO decision_rows VALUES (?, ?, ?)",
                    (key, symbol, payload),
                )

    def record_outcome(
        self,
        snapshot_id: str,
        symbol: str,
        payload: dict[str, object],
        *,
        recorded_at: datetime,
    ) -> None:
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with closing(sqlite3.connect(self.path)) as db, db:
            existing = db.execute(
                "SELECT payload FROM shadow_outcomes WHERE snapshot_id = ? AND symbol = ?",
                (snapshot_id, symbol.upper()),
            ).fetchone()
            if existing is not None and existing[0] != encoded:
                raise ValueError("immutable research outcome already exists with different data")
            db.execute(
                "INSERT OR IGNORE INTO shadow_outcomes VALUES (?, ?, ?, ?)",
                (
                    snapshot_id,
                    symbol.upper(),
                    recorded_at.astimezone(UTC).isoformat(),
                    encoded,
                ),
            )

    def outcome_count(self, snapshot_id: str) -> int:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT COUNT(*) FROM shadow_outcomes WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
        return 0 if row is None else int(row[0])
