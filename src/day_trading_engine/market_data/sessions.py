from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from day_trading_engine.providers.questrade import Market

_EASTERN = ZoneInfo("America/New_York")


class SessionPhase(StrEnum):
    PRE_MARKET = "PRE_MARKET"
    REGULAR = "REGULAR"
    POST_MARKET = "POST_MARKET"


@dataclass(frozen=True)
class SessionSchedule:
    session: str
    extended_open: str
    regular_open: str
    regular_close: str
    extended_close: str
    source: str

    def __post_init__(self) -> None:
        values = tuple(datetime.fromisoformat(value) for value in self.bounds)
        if any(value.tzinfo is None or value.utcoffset() is None for value in values):
            raise ValueError("session schedule bounds must be timezone-aware")
        if not values[0] <= values[1] < values[2] <= values[3]:
            raise ValueError("session schedule bounds are out of order")
        if any(value.astimezone(_EASTERN).date().isoformat() != self.session for value in values):
            raise ValueError("session schedule bounds must use one Eastern trading date")
        if not self.source.strip():
            raise ValueError("session schedule source is required")

    @property
    def bounds(self) -> tuple[str, str, str, str]:
        return self.extended_open, self.regular_open, self.regular_close, self.extended_close

    def phase(self, value: datetime) -> SessionPhase | None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("session phase timestamp must be timezone-aware")
        extended_open, regular_open, regular_close, extended_close = (
            datetime.fromisoformat(item) for item in self.bounds
        )
        observed = value.astimezone(_EASTERN)
        if extended_open <= observed < regular_open:
            return SessionPhase.PRE_MARKET
        if regular_open <= observed < regular_close:
            return SessionPhase.REGULAR
        if regular_close <= observed < extended_close:
            return SessionPhase.POST_MARKET
        return None


def canonical_schedule(session: date, regular_open: time, regular_close: time) -> SessionSchedule:
    def bound(value: time) -> str:
        return datetime.combine(session, value, tzinfo=_EASTERN).isoformat()

    return SessionSchedule(
        session=session.isoformat(),
        extended_open=bound(time(4)),
        regular_open=bound(regular_open),
        regular_close=bound(regular_close),
        extended_close=bound(time(20)),
        source="canonical_us_equities_v1",
    )


def schedule_from_markets(markets: tuple[Market, ...], *, session: date) -> SessionSchedule:
    candidates: list[tuple[datetime, datetime, datetime, datetime]] = []
    for market in markets:
        if market.currency.upper() != "USD":
            continue
        if not all(
            (market.extendedStartTime, market.startTime, market.endTime, market.extendedEndTime)
        ):
            continue
        values = tuple(
            datetime.fromisoformat(value)  # type: ignore[arg-type]
            for value in (
                market.extendedStartTime,
                market.startTime,
                market.endTime,
                market.extendedEndTime,
            )
        )
        if any(value.tzinfo is None or value.utcoffset() is None for value in values):
            continue
        if all(value.astimezone(_EASTERN).date() == session for value in values):
            candidates.append(values)  # type: ignore[arg-type]
    if not candidates:
        raise ValueError("Questrade returned no complete current-session extended schedule")
    normalized = {
        tuple(value.astimezone(_EASTERN) for value in row) for row in candidates
    }
    if len(normalized) != 1:
        raise ValueError("Questrade USD market schedules disagree")
    extended_open, regular_open, regular_close, extended_close = normalized.pop()
    return SessionSchedule(
        session=session.isoformat(),
        extended_open=extended_open.astimezone(_EASTERN).isoformat(),
        regular_open=regular_open.astimezone(_EASTERN).isoformat(),
        regular_close=regular_close.astimezone(_EASTERN).isoformat(),
        extended_close=extended_close.astimezone(_EASTERN).isoformat(),
        source="questrade_markets",
    )


def archive_schedule(schedule: SessionSchedule, root: Path) -> Path:
    target = root / f"{schedule.session}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False
    ) as handle:
        json.dump(asdict(schedule), handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        temporary = Path(handle.name)
    temporary.replace(target)
    return target
