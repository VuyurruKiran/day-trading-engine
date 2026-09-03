from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
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
from day_trading_engine.market_data.sessions import (
    SessionPhase,
    SessionSchedule,
    canonical_schedule,
)
from day_trading_engine.providers.alpaca_history import AlpacaHistoryClient


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
_MAX_ACCEPTED_GAP_MINUTES = 5
COVERED_STATUSES = frozenset({"complete", "accepted_gap", "accepted_sparse"})
HISTORY_CONTRACT_VERSION = 2


@dataclass(frozen=True)
class CoverageEntry:
    symbol: str
    provider: str
    provider_symbol_id: int | None
    feed: str
    session: str
    rows: int
    files: tuple[str, ...]
    checksums: dict[str, str]
    status: str
    missing_minutes: tuple[str, ...] = ()
    reason: str | None = None
    contract_version: int = HISTORY_CONTRACT_VERSION
    schedule_source: str = "canonical_us_equities_v1"
    requested_start: str | None = None
    requested_end: str | None = None
    phase_rows: dict[str, int] | None = None


@dataclass(frozen=True)
class CoverageInspection:
    status: str
    reason: str | None
    missing_minutes: tuple[str, ...] = ()


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


def _is_july_3_early_close(session: date) -> bool:
    if session.month != 7 or session.day != 3 or session.weekday() >= 5:
        return False
    return date(session.year, 7, 4).weekday() in {1, 2, 3, 4}


def _is_post_thanksgiving_early_close(session: date) -> bool:
    thanksgiving = USThanksgivingDay.dates(Timestamp(session), Timestamp(session))
    if thanksgiving.empty:
        thanksgiving = USThanksgivingDay.dates(
            Timestamp(date(session.year, 11, 1)), Timestamp(date(session.year, 11, 30))
        )
    return bool(len(thanksgiving) and session == thanksgiving[0].date() + timedelta(days=1))


def _is_christmas_eve_early_close(session: date) -> bool:
    return session.month == 12 and session.day == 24 and session.weekday() < 5


def _session_bounds(session: date) -> tuple[time, time]:
    if session in _NYSE_EXTRA_CLOSURES:
        raise ValueError("full-market closure has no trading session")
    close = time(16, 0)
    if (
        _is_july_3_early_close(session)
        or _is_post_thanksgiving_early_close(session)
        or _is_christmas_eve_early_close(session)
    ):
        close = time(13, 0)
    return time(9, 30), close


def _canonical_schedule(session: date) -> SessionSchedule:
    session_open, session_close = _session_bounds(session)
    return canonical_schedule(session, session_open, session_close)


def _phase_rows(candles: tuple[object, ...], schedule: SessionSchedule) -> dict[str, int]:
    counts = {phase.value: 0 for phase in SessionPhase}
    for candle in candles:
        start = getattr(candle, "start", None)
        end = getattr(candle, "end", None)
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise ValueError("historical candle timestamps are invalid")
        if end - start != timedelta(minutes=1):
            raise ValueError("historical candles must contain one-minute rows")
        phase = schedule.phase(start)
        if phase is None:
            raise ValueError("historical candle is outside the requested extended session")
        counts[phase.value] += 1
    return counts


def _inspect_extended_coverage(
    candles: tuple[object, ...], *, schedule: SessionSchedule
) -> tuple[CoverageInspection, dict[str, int]]:
    phase_rows = _phase_rows(candles, schedule)
    starts = [candle.start for candle in candles]  # type: ignore[attr-defined]
    if len(starts) != len(set(starts)):
        return (
            CoverageInspection("incomplete", "provider returned duplicate minute candles"),
            phase_rows,
        )
    if starts != sorted(starts):
        return CoverageInspection("incomplete", "one-minute candles are out of order"), phase_rows
    regular = tuple(
        candle
        for candle in candles
        if schedule.phase(candle.start) is SessionPhase.REGULAR  # type: ignore[attr-defined]
    )
    return (
        _inspect_coverage(
            regular,
            session_start=datetime.fromisoformat(schedule.regular_open),
            session_end=datetime.fromisoformat(schedule.regular_close),
        ),
        phase_rows,
    )

def _inspect_coverage(
    candles: tuple[object, ...],
    *,
    session_start: datetime,
    session_end: datetime,
) -> CoverageInspection:
    expected_rows = int((session_end - session_start).total_seconds() // 60)
    expected_starts = tuple(
        session_start + timedelta(minutes=index) for index in range(expected_rows)
    )
    if not candles:
        return CoverageInspection("missing", "provider returned no candles")

    if len(candles) > expected_rows:
        return CoverageInspection(
            "incomplete",
            f"expected {expected_rows} one-minute candles, received {len(candles)}",
        )

    actual_starts: list[datetime] = []
    for index, candle in enumerate(candles):
        start = getattr(candle, "start", None)
        end = getattr(candle, "end", None)
        if (
            not isinstance(start, datetime)
            or not isinstance(end, datetime)
            or start not in expected_starts
            or end - start != timedelta(minutes=1)
        ):
            return CoverageInspection(
                "incomplete",
                f"one-minute coverage gap or wrong range at row {index}",
            )
        actual_starts.append(start)

    if actual_starts != sorted(actual_starts):
        return CoverageInspection("incomplete", "one-minute candles are out of order")

    if len(set(actual_starts)) != len(actual_starts):
        return CoverageInspection("incomplete", "provider returned duplicate minute candles")

    actual_start_set = set(actual_starts)
    missing_minutes = tuple(
        expected_start.isoformat()
        for expected_start in expected_starts
        if expected_start not in actual_start_set
    )
    if missing_minutes:
        return CoverageInspection(
            "incomplete",
            f"provider missing {len(missing_minutes)} minute(s)",
            missing_minutes,
        )

    return CoverageInspection("complete", None)


def _accept_coverage_gap(
    first: CoverageInspection, second: CoverageInspection
) -> CoverageInspection | None:
    if first.status not in {"missing", "incomplete"}:
        return None
    if second.status not in {"missing", "incomplete"}:
        return None
    if not first.missing_minutes or first.missing_minutes != second.missing_minutes:
        return None
    if len(first.missing_minutes) > _MAX_ACCEPTED_GAP_MINUTES:
        return None
    return CoverageInspection("accepted_gap", "provider missing minute", first.missing_minutes)


def _classify_rechecked_gap(
    client: object,
    symbol: str,
    first: CoverageInspection,
    second: CoverageInspection,
) -> CoverageInspection | None:
    if first.missing_minutes and first.missing_minutes == second.missing_minutes:
        verifier = getattr(client, "missing_minutes_have_no_bar_eligible_trades", None)
        if callable(verifier):
            try:
                if verifier(symbol, first.missing_minutes):
                    return CoverageInspection(
                        "accepted_sparse",
                        "no bar-eligible trades in missing minute(s)",
                        first.missing_minutes,
                    )
                return None
            except Exception as exc:
                return CoverageInspection(
                    "incomplete",
                    f"bar-eligibility verification failed: {type(exc).__name__}: {exc}",
                    first.missing_minutes,
                )
    return _accept_coverage_gap(first, second)


def _is_covered_status(status: object) -> bool:
    return status in COVERED_STATUSES


def _trim_inclusive_close_candle(
    candles: tuple[object, ...], *, session_end: datetime
) -> tuple[object, ...]:
    """Drop an inclusive endTime candle when it starts exactly at session close."""
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
    *,
    request_start: date,
    request_end: date,
) -> dict[str, object]:
    accepted_gap_entries = [
        {
            "key": key,
            "symbol": str(entry.get("symbol", "")).upper(),
            "provider": str(entry.get("provider", "")),
            "provider_symbol_id": entry.get("provider_symbol_id"),
            "session": str(entry.get("session", "")),
            "status": str(entry.get("status", "")),
            "rows": int(entry.get("rows", 0) or 0),
            "reason": entry.get("reason"),
            "missing_minutes": list(entry.get("missing_minutes", ())),
        }
        for key, entry in sorted(entries.items())
        if entry.get("status") == "accepted_gap"
    ]
    accepted_sparse_entries = [
        {
            "key": key,
            "symbol": str(entry.get("symbol", "")).upper(),
            "provider": str(entry.get("provider", "")),
            "provider_symbol_id": entry.get("provider_symbol_id"),
            "session": str(entry.get("session", "")),
            "status": str(entry.get("status", "")),
            "rows": int(entry.get("rows", 0) or 0),
            "reason": entry.get("reason"),
            "missing_minutes": list(entry.get("missing_minutes", ())),
        }
        for key, entry in sorted(entries.items())
        if entry.get("status") == "accepted_sparse"
    ]
    covered_dates = sorted(
        {
            date.fromisoformat(str(entries[key]["session"]))
            for key in expected_keys
            if _is_covered_status(entries.get(key, {}).get("status"))
        }
    )
    span_days = (covered_dates[-1] - covered_dates[0]).days if len(covered_dates) > 1 else 0
    continuous_request = expected_keys == _required_keys(expected_keys)
    all_expected_complete = bool(expected_keys) and all(
        entries.get(key, {}).get("status") == "complete" for key in expected_keys
    )
    all_expected_covered = bool(expected_keys) and all(
        _is_covered_status(entries.get(key, {}).get("status")) for key in expected_keys
    )
    return {
        "version": HISTORY_CONTRACT_VERSION,
        "fidelity": "BAR_ONLY",
        "feature_availability": {
            "ohlcv": True,
            "historical_bid_ask": False,
            "historical_quote_size": False,
            "historical_provider_latency": False,
        },
        "coverage": {
            "first_complete_session": covered_dates[0].isoformat() if covered_dates else None,
            "last_complete_session": covered_dates[-1].isoformat() if covered_dates else None,
            "span_days": span_days,
            "expected": len(expected_keys),
            "all_expected_complete": all_expected_complete,
            "all_expected_covered": all_expected_covered,
            "continuous_expected_sessions": continuous_request,
            "current_request_complete": bool(current_keys)
            and all(_is_covered_status(entries.get(key, {}).get("status")) for key in current_keys),
            "accepted_gap_entries": accepted_gap_entries,
            "accepted_sparse_entries": accepted_sparse_entries,
            "meets_12_month_target": (
                continuous_request
                and bool(expected_keys)
                and all_expected_covered
                and request_end >= request_start + relativedelta(months=12)
            ),
            "meets_24_month_preferred_target": (
                continuous_request
                and bool(expected_keys)
                and all_expected_covered
                and request_end >= request_start + relativedelta(months=24)
            ),
            "complete": sum(item.get("status") == "complete" for item in entries.values()),
            "accepted_gap": sum(item.get("status") == "accepted_gap" for item in entries.values()),
            "accepted_sparse": sum(
                item.get("status") == "accepted_sparse" for item in entries.values()
            ),
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


def _normalize_symbols(values: Iterable[str]) -> tuple[str, ...]:
    symbols: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbols must be non-empty")
        if "=" in symbol:
            raise ValueError("symbols must be bare tickers")
        if symbol in seen:
            raise ValueError(f"duplicate symbol {symbol}")
        seen.add(symbol)
        symbols.append(symbol)
    if not symbols:
        raise ValueError("symbols are required")
    return tuple(symbols)


def backfill_one_minute_history(
    client: AlpacaHistoryClient,
    *,
    symbols: Iterable[str],
    start: date,
    end: date,
    root: Path,
    exchange_timezone: str = "America/New_York",
) -> Path:
    """Resume one-minute history by NYSE session and persist coverage/checksum evidence."""
    symbols = _normalize_symbols(symbols)
    provider = str(getattr(client, "provider", type(client).__name__))
    feed = str(getattr(client, "feed", "") or "unknown")
    sessions = _sessions(start, end)
    current_keys = {f"{session.isoformat()}:{symbol}" for symbol in symbols for session in sessions}
    tz = ZoneInfo(exchange_timezone)
    manifest_path = root / "coverage_manifest.json"
    existing: dict[str, dict[str, object]] = {}
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing = {str(item["key"]): item for item in payload.get("entries", [])}
    expected_keys = _normalize_expected_keys(set(current_keys))

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
                candles = _trim_inclusive_close_candle(
                    batch.candles,
                    session_end=session_end,
                )
                inspection, phase_rows = _inspect_extended_coverage(
                    candles, schedule=schedule
                )
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
                        retry_inspection = inspection
                        retry_phase_rows = phase_rows
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
                entry = CoverageEntry(
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
                entry = CoverageEntry(
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
                    phase_rows={phase.value: 0 for phase in SessionPhase},
                )
            existing[key] = {"key": key, **asdict(entry)}
            _write_manifest(
                manifest_path,
                _manifest_payload(
                    existing,
                    expected_keys,
                    current_keys,
                    request_start=start,
                    request_end=end,
                ),
            )
    _write_manifest(
        manifest_path,
        _manifest_payload(
            existing,
            expected_keys,
            current_keys,
            request_start=start,
            request_end=end,
        ),
    )
    return manifest_path


def write_universe_manifest(
    symbols: Iterable[str], *, as_of: date, root: Path, provider: str = "alpaca"
) -> Path:
    """Persist and accumulate the dated tested universe without losing earlier batches."""
    symbols = _normalize_symbols(symbols)
    target = root / "universe" / f"{as_of.isoformat()}.json"
    merged = {symbol: None for symbol in symbols}
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
            item_provider = item.get("provider", provider)
            if not isinstance(symbol, str) or not isinstance(item_provider, str):
                raise ValueError("existing universe manifest symbol is invalid")
            normalized = symbol.upper()
            if item_provider != provider:
                raise ValueError("existing universe manifest provider is invalid")
            merged.setdefault(normalized, None)
    payload: dict[str, object] = {
        "as_of": as_of.isoformat(),
        "symbols": [
            {"symbol": symbol, "provider": provider, "provider_symbol_id": None}
            for symbol in sorted(merged)
        ],
        "survivorship_risk": (
            "survivorship bias risk: provider historical-universe coverage may be incomplete"
        ),
    }
    _write_manifest(target, payload)
    return target
