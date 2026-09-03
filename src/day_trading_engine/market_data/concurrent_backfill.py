from __future__ import annotations

import json
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from day_trading_engine.market_data.backfill import (
    HISTORY_CONTRACT_VERSION,
    CoverageEntry,
    _canonical_schedule,
    _checksum,
    _classify_rechecked_gap,
    _entry_files_valid,
    _inspect_extended_coverage,
    _is_covered_status,
    _manifest_payload,
    _normalize_expected_keys,
    _normalize_symbols,
    _sessions,
    _trim_inclusive_close_candle,
    _write_manifest,
)
from day_trading_engine.market_data.historical_candles import write_candles_to_parquet
from day_trading_engine.providers.alpaca_history import AlpacaHistoryClient

_DEFAULT_WORKERS = 4
_MAX_WORKERS = 8
_MANIFEST_CHECKPOINT_ENTRIES = 100


def _fetch_session(
    client: AlpacaHistoryClient,
    *,
    symbol: str,
    session: date,
    root: Path,
    tz: ZoneInfo,
    provider: str,
    feed: str,
) -> CoverageEntry:
    """Fetch, validate, and persist one independent symbol/session partition."""
    schedule = _canonical_schedule(session)
    session_start = datetime.fromisoformat(schedule.extended_open).astimezone(tz)
    session_end = datetime.fromisoformat(schedule.extended_close).astimezone(tz)
    try:
        batch = client.get_candles(
            symbol,
            start=session_start,
            end=session_end,
            interval="OneMinute",
        )
        candles = _trim_inclusive_close_candle(batch.candles, session_end=session_end)
        inspection, phase_rows = _inspect_extended_coverage(candles, schedule=schedule)
        if inspection.status != "complete":
            retry_inspection = inspection
            retry_candles = candles
            retry_phase_rows = phase_rows
            accepted_gap = None
            try:
                retry_batch = client.get_candles(
                    symbol,
                    start=session_start,
                    end=session_end,
                    interval="OneMinute",
                )
                retry_candles = _trim_inclusive_close_candle(
                    retry_batch.candles,
                    session_end=session_end,
                )
                retry_inspection, retry_phase_rows = _inspect_extended_coverage(
                    retry_candles, schedule=schedule
                )
                accepted_gap = _classify_rechecked_gap(
                    client, symbol, inspection, retry_inspection
                )
            except Exception:
                pass
            inspection = accepted_gap or retry_inspection
            candles = retry_candles
            phase_rows = retry_phase_rows

        outputs = (
            write_candles_to_parquet(
                candles,
                root / "market",
                symbol=symbol,
                interval="OneMinute",
                provider=provider,
                feed=feed,
                schedule=schedule,
            )
            if _is_covered_status(inspection.status)
            else ()
        )
        relative = tuple(path.relative_to(root).as_posix() for path in outputs)
        return CoverageEntry(
            symbol=symbol,
            provider=provider,
            provider_symbol_id=None,
            feed=feed,
            session=session.isoformat(),
            rows=len(candles),
            files=relative,
            checksums={name: _checksum(root / name) for name in relative},
            status=inspection.status,
            missing_minutes=inspection.missing_minutes,
            reason=inspection.reason,
            schedule_source=schedule.source,
            requested_start=schedule.extended_open,
            requested_end=schedule.extended_close,
            phase_rows=phase_rows,
        )
    except Exception as exc:
        return CoverageEntry(
            symbol=symbol,
            provider=provider,
            provider_symbol_id=None,
            feed=feed,
            session=session.isoformat(),
            rows=0,
            files=(),
            checksums={},
            status="failed",
            reason=f"{type(exc).__name__}: {exc}",
            schedule_source=schedule.source,
            requested_start=schedule.extended_open,
            requested_end=schedule.extended_close,
            phase_rows={"PRE_MARKET": 0, "REGULAR": 0, "POST_MARKET": 0},
        )


def backfill_one_minute_history_concurrent(
    client: AlpacaHistoryClient,
    *,
    symbols: Iterable[str],
    start: date,
    end: date,
    root: Path,
    exchange_timezone: str = "America/New_York",
    workers: int = _DEFAULT_WORKERS,
) -> Path:
    """Backfill independent symbol/session partitions concurrently and resume safely."""
    if not 1 <= workers <= _MAX_WORKERS:
        raise ValueError(f"workers must be between 1 and {_MAX_WORKERS}")

    symbols = _normalize_symbols(symbols)
    provider = str(getattr(client, "provider", type(client).__name__))
    feed = str(getattr(client, "feed", "") or "unknown")
    sessions = _sessions(start, end)
    current_keys = {f"{session.isoformat()}:{symbol}" for symbol in symbols for session in sessions}
    expected_keys = _normalize_expected_keys(set(current_keys))
    tz = ZoneInfo(exchange_timezone)
    manifest_path = root / "coverage_manifest.json"

    existing: dict[str, dict[str, object]] = {}
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing = {str(item["key"]): item for item in payload.get("entries", [])}

    pending: list[tuple[str, date]] = []
    for symbol in sorted(symbols):
        for session in sessions:
            key = f"{session.isoformat()}:{symbol}"
            previous = existing.get(key)
            if (
                previous is not None
                and previous.get("contract_version") == HISTORY_CONTRACT_VERSION
                and _is_covered_status(previous.get("status"))
                and previous.get("provider") == provider
                and previous.get("feed") == feed
                and _entry_files_valid(previous, root)
            ):
                continue
            pending.append((symbol, session))

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="backfill") as executor:
        futures = {
            executor.submit(
                _fetch_session,
                client,
                symbol=symbol,
                session=session,
                root=root,
                tz=tz,
                provider=provider,
                feed=feed,
            ): (symbol, session)
            for symbol, session in pending
        }
        completed_since_checkpoint = 0
        for future in as_completed(futures):
            symbol, session = futures[future]
            key = f"{session.isoformat()}:{symbol}"
            entry = future.result()
            existing[key] = {"key": key, **asdict(entry)}
            completed_since_checkpoint += 1
            if completed_since_checkpoint >= _MANIFEST_CHECKPOINT_ENTRIES:
                _write_manifest(
                    manifest_path,
                    _manifest_payload(
                        dict(sorted(existing.items())),
                        expected_keys,
                        current_keys,
                        request_start=start,
                        request_end=end,
                    ),
                )
                completed_since_checkpoint = 0

    _write_manifest(
        manifest_path,
        _manifest_payload(
            dict(sorted(existing.items())),
            expected_keys,
            current_keys,
            request_start=start,
            request_end=end,
        ),
    )
    return manifest_path
