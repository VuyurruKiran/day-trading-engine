from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from .models import ContextRecord


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ContextStore:
    def __init__(self, path: str | Path) -> None:
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
                url TEXT,
                payload TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                UNIQUE(kind, dedupe_key)
            )
            """
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
        self._connection.close()

    def __enter__(self) -> ContextStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add_many(self, records: Iterable[ContextRecord]) -> int:
        before = self._connection.total_changes
        with self._connection:
            self._connection.executemany(
                """
                INSERT OR IGNORE INTO context_records
                (kind, provider, external_id, title, source_at, received_at,
                 symbols, url, payload, dedupe_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        record.kind,
                        record.provider,
                        record.external_id,
                        record.title,
                        _iso(record.source_at),
                        _iso(record.received_at),
                        json.dumps(record.symbols),
                        record.url,
                        json.dumps(record.payload, sort_keys=True, separators=(",", ":")),
                        record.dedupe_key,
                    )
                    for record in records
                ),
            )
        return self._connection.total_changes - before

    def record_collection(
        self,
        *,
        run_at: datetime,
        record_count: int,
        errors: Iterable[str] = (),
        versions: Mapping[str, str] | None = None,
    ) -> None:
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
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        sql = (
            "SELECT kind, provider, external_id, title, source_at, received_at, symbols, url, "
            "payload FROM context_records WHERE received_at <= ?"
        )
        params: list[object] = [_iso(cutoff)]
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        sql += " ORDER BY received_at, provider, external_id"
        rows = self._connection.execute(sql, params).fetchall()
        return [
            ContextRecord(
                kind=row[0],
                provider=row[1],
                external_id=row[2],
                title=row[3],
                source_at=_parse(row[4]),
                received_at=_parse(row[5]),
                symbols=tuple(json.loads(row[6])),
                url=row[7],
                payload=json.loads(row[8]),
            )
            for row in rows
        ]
