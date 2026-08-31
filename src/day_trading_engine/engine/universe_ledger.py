from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from day_trading_engine.engine.universe import UniverseSnapshot


class UniverseLedger:
    """Persist security identity and point-in-time universe membership transitions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS securities (
                    security_id TEXT PRIMARY KEY,
                    current_symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    sector TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS symbol_history (
                    security_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    reason TEXT NOT NULL,
                    PRIMARY KEY(security_id, effective_from)
                );
                CREATE TABLE IF NOT EXISTS memberships (
                    universe_id TEXT NOT NULL,
                    security_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    score REAL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY(universe_id, security_id)
                );
                """
            )

    def record_snapshot(self, snapshot: UniverseSnapshot) -> None:
        effective = snapshot.effective_from
        with sqlite3.connect(self.path) as db:
            # Every snapshot is a new immutable membership version. Close the prior
            # active version before inserting the new one, even for retained members.
            db.execute(
                "UPDATE memberships SET effective_to = ? WHERE effective_to IS NULL",
                (effective,),
            )
            for row in snapshot.members:
                db.execute(
                    "INSERT OR REPLACE INTO securities VALUES (?, ?, ?, ?, ?)",
                    (row.security_id, row.symbol, row.exchange, row.asset_type, row.sector),
                )
                current = db.execute(
                    "SELECT symbol FROM symbol_history WHERE security_id = ? "
                    "AND effective_to IS NULL",
                    (row.security_id,),
                ).fetchone()
                if current is None:
                    db.execute(
                        "INSERT INTO symbol_history VALUES (?, ?, ?, NULL, ?)",
                        (row.security_id, row.symbol, effective, "snapshot"),
                    )
                elif current[0] != row.symbol:
                    db.execute(
                        "UPDATE symbol_history SET effective_to = ? "
                        "WHERE security_id = ? AND effective_to IS NULL",
                        (effective, row.security_id),
                    )
                    db.execute(
                        "INSERT INTO symbol_history VALUES (?, ?, ?, NULL, ?)",
                        (row.security_id, row.symbol, effective, "ticker_change"),
                    )
                db.execute(
                    "INSERT OR REPLACE INTO memberships VALUES (?, ?, ?, ?, NULL, ?, ?)",
                    (
                        snapshot.universe_id,
                        row.security_id,
                        row.symbol,
                        effective,
                        row.score,
                        row.reason,
                    ),
                )

    def record_delisting(self, security_id: str, *, effective_on: date, reason: str) -> None:
        if not reason.strip():
            raise ValueError("delisting reason is required")
        effective = effective_on.isoformat()
        with sqlite3.connect(self.path) as db:
            if db.execute(
                "SELECT 1 FROM securities WHERE security_id = ?", (security_id,)
            ).fetchone() is None:
                raise KeyError(security_id)
            db.execute(
                "UPDATE memberships SET effective_to = ? "
                "WHERE security_id = ? AND effective_to IS NULL",
                (effective, security_id),
            )
            db.execute(
                "UPDATE symbol_history SET effective_to = ?, reason = ? "
                "WHERE security_id = ? AND effective_to IS NULL",
                (effective, reason, security_id),
            )

    def membership_as_of(self, as_of: date) -> tuple[tuple[str, str], ...]:
        value = as_of.isoformat()
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT security_id, symbol FROM memberships "
                "WHERE effective_from <= ? AND (effective_to IS NULL OR effective_to > ?) "
                "ORDER BY symbol, security_id",
                (value, value),
            ).fetchall()
        return tuple((str(row[0]), str(row[1])) for row in rows)

    def history(self, security_id: str) -> tuple[tuple[str, str, str | None, str], ...]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT symbol, effective_from, effective_to, reason FROM symbol_history "
                "WHERE security_id = ? ORDER BY effective_from",
                (security_id,),
            ).fetchall()
        return tuple((str(row[0]), str(row[1]), row[2], str(row[3])) for row in rows)
