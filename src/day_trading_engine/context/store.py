from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from .models import ContextRecord

_GLOBAL_NEWS_ASSOCIATION = "*"


def _iso(value: datetime) -> str:
    """Serialize a datetime as a normalized UTC ISO-8601 string."""
    return value.astimezone(UTC).isoformat()


def _parse(value: str) -> datetime:
    """Parse an ISO-8601 datetime stored by this module."""
    return datetime.fromisoformat(value)


def _association(source_at: str, received_at: str) -> dict[str, str]:
    """Return paired source/receipt timestamps for one evidence association."""
    return {"source_at": source_at, "received_at": received_at}


def _availability_key(
    source_at: str,
    received_at: str,
) -> tuple[datetime, datetime, datetime]:
    """Order evidence by when both publication and receipt are known."""
    source = _parse(source_at)
    received = _parse(received_at)
    return max(source, received), received, source


def _decode_associations(
    raw: str,
    *,
    row_source_at: str,
    row_received_at: str,
    symbols: tuple[str, ...],
) -> dict[str, dict[str, str]]:
    """Decode association timestamps, including the legacy receipt-only format."""
    decoded: dict[str, dict[str, str]] = {}
    for key, value in dict(json.loads(raw or "{}")).items():
        if isinstance(value, str):
            # Legacy rows only stored receipt time. Using it as source time is
            # conservative and does not make evidence visible before receipt.
            decoded[key] = _association(value, value)
            continue
        if not isinstance(value, dict):
            raise ValueError("invalid context association timestamp metadata")
        source_at = value.get("source_at")
        received_at = value.get("received_at")
        if not isinstance(source_at, str) or not isinstance(received_at, str):
            raise ValueError("invalid context association timestamp metadata")
        decoded[key] = _association(source_at, received_at)
    for symbol in symbols:
        decoded.setdefault(symbol, _association(row_source_at, row_received_at))
    return decoded


def _earlier_association(
    current: dict[str, str] | None,
    *,
    source_at: str,
    received_at: str,
) -> dict[str, str]:
    """Keep the association pair that became available first."""
    incoming = _association(source_at, received_at)
    if current is None:
        return incoming
    current_key = _availability_key(current["source_at"], current["received_at"])
    incoming_key = _availability_key(source_at, received_at)
    return incoming if incoming_key < current_key else current


def _known_by(times: dict[str, str], cutoff: datetime) -> bool:
    """Return whether both source and receipt times are at or before cutoff."""
    return _parse(times["source_at"]) <= cutoff and _parse(times["received_at"]) <= cutoff


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
        """Insert new records and merge news ticker associations deterministically."""
        added = 0
        with self._connection:
            for record in records:
                if record.kind == "news":
                    existing = self._connection.execute(
                        """
                        SELECT provider, external_id, title, source_at, received_at,
                               symbols, symbol_received_at, url, payload
                        FROM context_records
                        WHERE kind = ? AND dedupe_key = ?
                        """,
                        (record.kind, record.dedupe_key),
                    ).fetchone()
                    if existing is not None:
                        existing_symbols = tuple(json.loads(existing[5]))
                        association_times = _decode_associations(
                            existing[6],
                            row_source_at=existing[3],
                            row_received_at=existing[4],
                            symbols=existing_symbols,
                        )
                        if not existing_symbols and not association_times:
                            association_times[_GLOBAL_NEWS_ASSOCIATION] = _association(
                                existing[3], existing[4]
                            )

                        record_received_at = _iso(record.received_at)
                        record_source_at = _iso(record.source_at)
                        merged_symbols = list(existing_symbols)
                        changed = False

                        if record.symbols:
                            for symbol in record.symbols:
                                current = association_times.get(symbol)
                                updated = _earlier_association(
                                    current,
                                    source_at=record_source_at,
                                    received_at=record_received_at,
                                )
                                if current != updated:
                                    association_times[symbol] = updated
                                    changed = True
                                if symbol not in merged_symbols:
                                    merged_symbols.append(symbol)
                                    changed = True
                        else:
                            current = association_times.get(_GLOBAL_NEWS_ASSOCIATION)
                            updated = _earlier_association(
                                current,
                                source_at=record_source_at,
                                received_at=record_received_at,
                            )
                            if current != updated:
                                association_times[_GLOBAL_NEWS_ASSOCIATION] = updated
                                changed = True

                        existing_key = (
                            *_availability_key(existing[3], existing[4]),
                            existing[0],
                            existing[1],
                        )
                        record_key = (
                            *_availability_key(record_source_at, record_received_at),
                            record.provider,
                            record.external_id,
                        )
                        if record_key < existing_key:
                            changed = True
                            provider = record.provider
                            external_id = record.external_id
                            title = record.title
                            source_at = record_source_at
                            received_at = record_received_at
                            url = record.url
                            payload = json.dumps(
                                record.payload,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                        else:
                            provider = existing[0]
                            external_id = existing[1]
                            title = existing[2]
                            source_at = existing[3]
                            received_at = existing[4]
                            url = existing[7]
                            payload = existing[8]

                        if changed:
                            self._connection.execute(
                                """
                                UPDATE context_records
                                SET provider = ?, external_id = ?, title = ?,
                                    source_at = ?, received_at = ?, symbols = ?,
                                    symbol_received_at = ?, url = ?, payload = ?
                                WHERE kind = ? AND dedupe_key = ?
                                """,
                                (
                                    provider,
                                    external_id,
                                    title,
                                    source_at,
                                    received_at,
                                    json.dumps(tuple(merged_symbols)),
                                    json.dumps(association_times, sort_keys=True),
                                    url,
                                    payload,
                                    record.kind,
                                    record.dedupe_key,
                                ),
                            )
                        continue

                record_source_at = _iso(record.source_at)
                record_received_at = _iso(record.received_at)
                association_times = {
                    symbol: _association(record_source_at, record_received_at)
                    for symbol in record.symbols
                }
                if record.kind == "news" and not record.symbols:
                    association_times[_GLOBAL_NEWS_ASSOCIATION] = _association(
                        record_source_at, record_received_at
                    )
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
                        record_source_at,
                        record_received_at,
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
            if row[0] != "news":
                records.append(
                    ContextRecord(
                        kind=row[0],
                        provider=row[1],
                        external_id=row[2],
                        title=row[3],
                        source_at=_parse(row[4]),
                        received_at=_parse(row[5]),
                        symbols=stored_symbols,
                        url=row[8],
                        payload=json.loads(row[9]),
                    )
                )
                continue

            association_times = _decode_associations(
                row[7],
                row_source_at=row[4],
                row_received_at=row[5],
                symbols=stored_symbols,
            )
            global_times = association_times.get(_GLOBAL_NEWS_ASSOCIATION)
            if global_times is None and not stored_symbols:
                global_times = _association(row[4], row[5])
            if global_times is not None and _known_by(global_times, cutoff):
                records.append(
                    ContextRecord(
                        kind=row[0],
                        provider=row[1],
                        external_id=row[2],
                        title=row[3],
                        source_at=_parse(global_times["source_at"]),
                        received_at=_parse(global_times["received_at"]),
                        symbols=(),
                        url=row[8],
                        payload=json.loads(row[9]),
                    )
                )
                continue

            for symbol in stored_symbols:
                times = association_times[symbol]
                if not _known_by(times, cutoff):
                    continue
                records.append(
                    ContextRecord(
                        kind=row[0],
                        provider=row[1],
                        external_id=row[2],
                        title=row[3],
                        source_at=_parse(times["source_at"]),
                        received_at=_parse(times["received_at"]),
                        symbols=(symbol,),
                        url=row[8],
                        payload=json.loads(row[9]),
                    )
                )

        records.sort(
            key=lambda record: (
                record.received_at,
                record.source_at,
                record.provider,
                record.external_id,
                record.symbols,
            )
        )
        return records
