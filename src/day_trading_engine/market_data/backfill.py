from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pandas import Timestamp
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
    sunday_to_monday,
)

from day_trading_engine.market_data.historical_candles import write_candles_to_parquet
from day_trading_engine.providers.questrade_history import QuestradeHistoryClient


class _NyseHolidayCalendar(AbstractHolidayCalendar):
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday(
            "Juneteenth",
            month=6,
            day=19,
            start_date=Timestamp("2022-06-19"),
            observance=nearest_workday,
        ),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


_NYSE_EXTRA_CLOSURES = frozenset({date(2025, 1, 9)})


@dataclass(frozen=True)
class CoverageEntry:
    symbol: str
    symbol_id: int
    session: str
    rows: int
    files: tuple[str, ...]
    checksums: dict[str, str]
    status: str
    reason: str | None = None


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sessions(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end must not be before start")
    holidays = {
        value.date()
        for value in _NyseHolidayCalendar().holidays(start=Timestamp(start), end=Timestamp(end))
    }
    holidays.update(day for day in _NYSE_EXTRA_CLOSURES if start <= day <= end)
    result: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            result.append(current)
        current += timedelta(days=1)
    return result


def _session_close(session: date) -> time:
    thanksgiving = USThanksgivingDay.dates(Timestamp(session), Timestamp(session))
    if thanksgiving.empty:
        thanksgiving = USThanksgivingDay.dates(
            Timestamp(date(session.year, 11, 1)), Timestamp(date(session.year, 11, 30))
        )
    if len(thanksgiving) and session == thanksgiving[0].date() + timedelta(days=1):
        return time(13, 0)
    if session.month == 12 and session.day == 24 and session.weekday() < 5:
        return time(13, 0)
    return time(16, 0)


def _coverage_status(
    candles: tuple[object, ...],
    *,
    session_start: datetime,
    session_end: datetime,
) -> tuple[str, str | None]:
    if not candles:
        return "missing", "provider returned no candles"

    expected_rows = int((session_end - session_start).total_seconds() // 60)
    if len(candles) != expected_rows:
        return "incomplete", f"expected {expected_rows} one-minute candles, received {len(candles)}"
    for index, candle in enumerate(candles):
        expected_start = session_start + timedelta(minutes=index)
        expected_end = expected_start + timedelta(minutes=1)
        if (
            getattr(candle, "start", None) != expected_start
            or getattr(candle, "end", None) != expected_end
        ):
            return "incomplete", f"one-minute coverage gap or wrong range at row {index}"
    return "complete", None


def _trim_inclusive_close_candle(
    candles: tuple[object, ...], *, session_end: datetime
) -> tuple[object, ...]:
    """Drop Questrade's inclusive endTime candle when it starts exactly at session close."""
    if not candles:
        return candles
    last = candles[-1]
    if (
        getattr(last, "start", None) == session_end
        and getattr(last, "end", None) == session_end + timedelta(minutes=1)
    ):
        return candles[:-1]
    return candles


def _required_keys(expected_keys: set[str]) -> set[str]:
    if not expected_keys:
        return set()
    parsed = [key.split(":", 1) for key in expected_keys]
    dates = [date.fromisoformat(session) for session, _ in parsed]
    symbols = {symbol for _, symbol in parsed}
    return {
        f"{session.isoformat()}:{symbol}"
        for session in _sessions(min(dates), max(dates))
        for symbol in symbols
    }


def _normalize_expected_keys(keys: set[str]) -> set[str]:
    parsed: list[tuple[str, date]] = []
    for key in keys:
        session, separator, symbol = key.partition(":")
        if not separator or not symbol:
            continue
        try:
            parsed.append((key, date.fromisoformat(session)))
        except ValueError:
            continue
    if not parsed:
        return set()
    trading_days = set(_sessions(min(day for _, day in parsed), max(day for _, day in parsed)))
    return {key for key, day in parsed if day in trading_days}


def _manifest_payload(
    entries: dict[str, dict[str, object]],
    expected_keys: set[str],
    current_keys: set[str],
) -> dict[str, object]:
    complete_dates = sorted(
        {
            date.fromisoformat(str(entries[key]["session"]))
            for key in expected_keys
            if entries.get(key, {}).get("status") == "complete"
        }
    )
    span_days = (complete_dates[-1] - complete_dates[0]).days if len(complete_dates) > 1 else 0
    continuous_request = expected_keys == _required_keys(expected_keys)
    all_expected_complete = bool(expected_keys) and all(
        entries.get(key, {}).get("status") == "complete" for key in expected_keys
    )
    current_request_complete = all(
        entries.get(key, {}).get("status") == "complete" for key in current_keys
    )
    coverage_complete = continuous_request and all_expected_complete
    return {
        "version": 1,
        "fidelity": "BAR_ONLY",
        "feature_availability": {
            "ohlcv": True,
            "historical_bid_ask": False,
            "historical_quote_size": False,
            "historical_provider_latency": False,
        },
        "coverage": {
            "first_complete_session": complete_dates[0].isoformat() if complete_dates else None,
            "last_complete_session": complete_dates[-1].isoformat() if complete_dates else None,
            "span_days": span_days,
            "expected": len(expected_keys),
            "all_expected_complete": all_expected_complete,
            "continuous_expected_sessions": continuous_request,
            "current_request_complete": current_request_complete,
            "meets_12_month_target": coverage_complete and span_days >= 365,
            "meets_24_month_preferred_target": coverage_complete and span_days >= 730,
            "complete": sum(item.get("status") == "complete" for item in entries.values()),
            "missing": sum(item.get("status") == "missing" for item in entries.values()),
            "incomplete": sum(item.get("status") == "incomplete" for item in entries.values()),
            "failed": sum(item.get("status") == "failed" for item in entries.values()),
        },
        "expected_keys": sorted(expected_keys),
        "current_request_keys": sorted(current_keys),
        "entries": list(entries.values()),
    }


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
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


def _entry_files_valid(entry: dict[str, object], root: Path) -> bool:
    files = entry.get("files")
    checksums = entry.get("checksums")
    if not isinstance(files, list) or not files or not isinstance(checksums, dict):
        return False
    root_resolved = root.resolve()
    for relative in files:
        if not isinstance(relative, str) or checksums.get(relative) is None:
            return False
        path = (root / relative).resolve()
        if not path.is_relative_to(root_resolved) or not path.is_file():
            return False
        if _checksum(path) != checksums[relative]:
            return False
    return True


def backfill_one_minute_history(
    client: QuestradeHistoryClient,
    *,
    symbols: dict[str, int],
    start: date,
    end: date,
    root: Path,
    exchange_timezone: str = "America/New_York",
) -> Path:
    """Resume one-minute history by NYSE session and persist coverage/checksum evidence."""
    if not symbols:
        raise ValueError("symbols are required")
    sessions = _sessions(start, end)
    current_keys = {
        f"{session.isoformat()}:{symbol.upper()}"
        for symbol in symbols
        for session in sessions
    }
    tz = ZoneInfo(exchange_timezone)
    manifest_path = root / "coverage_manifest.json"
    existing: dict[str, dict[str, object]] = {}
    expected_keys = set(current_keys)
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing = {str(item["key"]): item for item in payload.get("entries", [])}
        previous_expected = payload.get("expected_keys")
        if isinstance(previous_expected, list):
            expected_keys.update(str(key) for key in previous_expected)
        else:
            expected_keys.update(existing)
    expected_keys = _normalize_expected_keys(expected_keys)

    for symbol, symbol_id in sorted(symbols.items()):
        if symbol_id <= 0:
            raise ValueError("symbol ids must be positive")
        for session in sessions:
            key = f"{session.isoformat()}:{symbol.upper()}"
            previous = existing.get(key)
            if (
                previous is not None
                and previous.get("status") == "complete"
                and previous.get("symbol_id") == symbol_id
                and _entry_files_valid(previous, root)
            ):
                continue
            session_start = datetime.combine(session, time(9, 30), tzinfo=tz)
            session_end = datetime.combine(session, _session_close(session), tzinfo=tz)
            try:
                batch = client.get_candles(
                    symbol_id,
                    start=session_start,
                    end=session_end,
                    interval="OneMinute",
                )
                candles = _trim_inclusive_close_candle(
                    batch.candles,
                    session_end=session_end,
                )
                status, reason = _coverage_status(
                    candles,
                    session_start=session_start,
                    session_end=session_end,
                )
                outputs = (
                    write_candles_to_parquet(
                        candles,
                        root / "market",
                        symbol=symbol,
                        interval="OneMinute",
                    )
                    if status == "complete"
                    else ()
                )
                relative = tuple(path.relative_to(root).as_posix() for path in outputs)
                entry = CoverageEntry(
                    symbol=symbol.upper(),
                    symbol_id=symbol_id,
                    session=session.isoformat(),
                    rows=len(candles),
                    files=relative,
                    checksums={name: _checksum(root / name) for name in relative},
                    status=status,
                    reason=reason,
                )
            except Exception as exc:
                entry = CoverageEntry(
                    symbol=symbol.upper(),
                    symbol_id=symbol_id,
                    session=session.isoformat(),
                    rows=0,
                    files=(),
                    checksums={},
                    status="failed",
                    reason=f"{type(exc).__name__}: {exc}",
                )
            existing[key] = {"key": key, **asdict(entry)}
            _write_manifest(
                manifest_path,
                _manifest_payload(existing, expected_keys, current_keys),
            )
    _write_manifest(manifest_path, _manifest_payload(existing, expected_keys, current_keys))
    return manifest_path


def write_universe_manifest(symbols: dict[str, int], *, as_of: date, root: Path) -> Path:
    """Persist and accumulate the dated tested universe without losing earlier batches."""
    if not symbols:
        raise ValueError("symbols are required")
    target = root / "universe" / f"{as_of.isoformat()}.json"
    merged = {symbol.upper(): symbol_id for symbol, symbol_id in symbols.items()}
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or existing.get("as_of") != as_of.isoformat():
            raise ValueError("existing universe manifest is invalid")
        existing_symbols = existing.get("symbols")
        if not isinstance(existing_symbols, list):
            raise ValueError("existing universe manifest symbols are invalid")
        for item in existing_symbols:
            if not isinstance(item, dict):
                raise ValueError("existing universe manifest symbol is invalid")
            symbol = item.get("symbol")
            symbol_id = item.get("symbol_id")
            if not isinstance(symbol, str) or not isinstance(symbol_id, int):
                raise ValueError("existing universe manifest symbol is invalid")
            normalized = symbol.upper()
            if normalized in merged and merged[normalized] != symbol_id:
                raise ValueError(f"conflicting symbol id for {normalized}")
            merged.setdefault(normalized, symbol_id)
    payload: dict[str, object] = {
        "as_of": as_of.isoformat(),
        "symbols": [
            {"symbol": symbol, "symbol_id": symbol_id}
            for symbol, symbol_id in sorted(merged.items())
        ],
        "survivorship_risk": (
            "survivorship bias risk: provider historical-universe coverage may be incomplete"
        ),
    }
    _write_manifest(target, payload)
    return target
