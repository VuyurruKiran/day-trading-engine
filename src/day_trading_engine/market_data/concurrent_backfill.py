from __future__ import annotations

import json
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from day_trading_engine.market_data.backfill import (
    CoverageEntry,
    _accept_coverage_gap,
    _checksum,
    _entry_files_valid,
    _inspect_coverage,
    _is_covered_status,
    _manifest_payload,
    _normalize_expected_keys,
    _normalize_symbols,
    _session_bounds,
    _sessions,
    _trim_inclusive_close_candle,
    _write_manifest,
)
from day_trading_engine.market_data.historical_candles import write_candles_to_parquet
from day_trading_engine.providers.alpaca_history import AlpacaHistoryClient

_DEFAULT_WORKERS = 4
_MAX_WORKERS = 8


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
    start_time, end_time = _session_bounds(session)
    session_start = datetime.combine(session, start_time, tzinfo=tz)
    session_end = datetime.combine(session, end_time, tzinfo=tz)
    try:
        batch = client.get_candles(
            symbol,
            start=session_start,
            end=session_end,
            interval="OneMinute",
        )
        candles = _trim_inclusive_close_candle(batch.candles, session_end=session_end)
        inspection = _inspect_coverage(
            candles,
            session_start=session_start,
            session_end=session_end,
        )
        if inspection.status != "complete":
            retry_inspection = inspection
            retry_candles = candles
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
                retry_inspection = _inspect_coverage(
                    retry_candles,
                    session_start=session_start,
                    session_end=session_end,
                )
            except Exception:
                pass
            inspection = _accept_coverage_gap(inspection, retry_inspection) or retry_inspection
            candles = retry_candles

        outputs = (
            write_candles_to_parquet(
                candles,
                root / "market",
                symbol=symbol,
                interval="OneMinute",
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
    feed = str(getattr(client, "feed", ""))
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
        for future in as_completed(futures):
            symbol, session = futures[future]
            key = f"{session.isoformat()}:{symbol}"
            entry = future.result()
            existing[key] = {"key": key, **asdict(entry)}
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
