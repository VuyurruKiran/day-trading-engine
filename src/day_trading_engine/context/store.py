from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from .models import ContextRecord


def _iso(value: datetime) -> str:
    """Serialize a datetime as a normalized UTC ISO-8601 string."""
    return value.astimezone(UTC).isoformat()


def _parse(value: str) -> datetime:
    """Parse an ISO-8601 datetime stored by this module."""
    return datetime.fromisoformat(value)


class ContextStore:
    def __init__(self, path: str | Path) -> None:
        """Open the context database and ensure its tables exist."""
        self._connection = sqlite3.connect(Path(path))
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS context_records (
                kind TEXT NOT NULL,
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                source_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                symbols TEXT NOT NULL,
                symbol_received_at TEXT NOT NULL DEFAULT '{}',
                url TEXT,
                payload TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                UNIQUE(kind, dedupe_key)
            )
            """
        )
        columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(context_records)")
        }
        if "symbol_received_at" not in columns:
            self._connection.execute(
                "ALTER TABLE context_records "
                "ADD COLUMN symbol_received_at TEXT NOT NULL DEFAULT '{}'"
            )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_context_received_at ON context_records(received_at)"
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS context_collection_runs (
                run_at TEXT NOT NULL,
                record_count INTEGER NOT NULL,
                errors TEXT NOT NULL,
                versions TEXT NOT NULL
            )
            """
        )

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._connection.close()

    def __enter__(self) -> ContextStore:
        """Return this store for context-manager usage."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the store when leaving a context-manager block."""
        self.close()

    def add_many(self, records: Iterable[ContextRecord]) -> int:
        """Insert new records and merge later news ticker associations safely."""
        added = 0
        with self._connection:
            for record in records:
                if record.kind == "news":
                    existing = self._connection.execute(
                        """
                        SELECT symbols, symbol_received_at, received_at
                        FROM context_records
                        WHERE kind = ? AND dedupe_key = ?
                        """,
                        (record.kind, record.dedupe_key),
                    ).fetchone()
                    if existing is not None:
                        existing_symbols = tuple(json.loads(existing[0]))
                        association_times = dict(json.loads(existing[1] or "{}"))
                        for symbol in existing_symbols:
                            association_times.setdefault(symbol, existing[2])
                        changed = False
                        merged_symbols = list(existing_symbols)
                        for symbol in record.symbols:
                            if symbol not in association_times:
                                association_times[symbol] = _iso(record.received_at)
                                merged_symbols.append(symbol)
                                changed = True
                        if changed:
                            self._connection.execute(
                                """
                                UPDATE context_records
                                SET symbols = ?, symbol_received_at = ?
                                WHERE kind = ? AND dedupe_key = ?
                                """,
                                (
                                    json.dumps(tuple(merged_symbols)),
                                    json.dumps(association_times, sort_keys=True),
                                    record.kind,
                                    record.dedupe_key,
                                ),
                            )
                        continue

                association_times = {
                    symbol: _iso(record.received_at) for symbol in record.symbols
                }
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO context_records
                    (kind, provider, external_id, title, source_at, received_at,
                     symbols, symbol_received_at, url, payload, dedupe_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.kind,
                        record.provider,
                        record.external_id,
                        record.title,
                        _iso(record.source_at),
                        _iso(record.received_at),
                        json.dumps(record.symbols),
                        json.dumps(association_times, sort_keys=True),
                        record.url,
                        json.dumps(
                            record.payload,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        record.dedupe_key,
                    ),
                )
                if cursor.rowcount > 0:
                    added += cursor.rowcount
        return added

    def record_collection(
        self,
        *,
        run_at: datetime,
        record_count: int,
        errors: Iterable[str] = (),
        versions: Mapping[str, str] | None = None,
    ) -> None:
        """Persist collection counts, provider errors, and version metadata."""
        if run_at.tzinfo is None or run_at.utcoffset() is None:
            raise ValueError("run_at must be timezone-aware")
        if record_count < 0:
            raise ValueError("record_count cannot be negative")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO context_collection_runs(run_at, record_count, errors, versions)
                VALUES (?, ?, ?, ?)
                """,
                (
                    _iso(run_at),
                    record_count,
                    json.dumps(tuple(errors)),
                    json.dumps(dict(versions or {}), sort_keys=True),
                ),
            )

    def as_of(self, cutoff: datetime, *, kinds: tuple[str, ...] = ()) -> list[ContextRecord]:
        """Return records both published and received no later than the cutoff."""
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        sql = (
            "SELECT kind, provider, external_id, title, source_at, received_at, symbols, "
            "symbol_received_at, url, payload FROM context_records "
            "WHERE received_at <= ? AND source_at <= ?"
        )
        cutoff_iso = _iso(cutoff)
        params: list[object] = [cutoff_iso, cutoff_iso]
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        sql += " ORDER BY received_at, provider, external_id"
        rows = self._connection.execute(sql, params).fetchall()

        records: list[ContextRecord] = []
        for row in rows:
            stored_symbols = tuple(json.loads(row[6]))
            association_times = dict(json.loads(row[7] or "{}"))
            if association_times:
                symbols = tuple(
                    symbol
                    for symbol in stored_symbols
                    if _parse(association_times.get(symbol, row[5])) <= cutoff
                )
            else:
                symbols = stored_symbols
            if stored_symbols and not symbols:
                continue
            records.append(
                ContextRecord(
                    kind=row[0],
                    provider=row[1],
                    external_id=row[2],
                    title=row[3],
                    source_at=_parse(row[4]),
                    received_at=_parse(row[5]),
                    symbols=symbols,
                    url=row[8],
                    payload=json.loads(row[9]),
                )
            )
        return records
