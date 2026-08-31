from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from day_trading_engine.core.config import AppConfig
from day_trading_engine.engine.cohort import CohortResult, ResearchCandidate, build_research_cohort
from day_trading_engine.engine.universe import load_universe_snapshot
from day_trading_engine.market_data.store import StoredQuote

_SCAN_SIZE = 200


@dataclass(frozen=True, slots=True)
class BroadScanMetrics:
    rvol: float | None = None
    volume_acceleration: float | None = None
    relative_strength: float | None = None


@dataclass(frozen=True, slots=True)
class BroadScanScore:
    symbol: str
    score: float
    components: dict[str, float]
    valid: bool
    reason: str


def _validate_research_symbols(symbols: tuple[str, ...], config: AppConfig) -> tuple[str, ...]:
    if len(symbols) != config.research_universe.target:
        raise ValueError("active research universe does not contain the configured target")
    if len(symbols) != len(set(symbols)):
        raise ValueError("research universe cannot contain duplicate symbols")
    overlap = set(symbols) & set(config.research_universe.benchmark_symbols)
    if overlap:
        raise ValueError("benchmark symbols must remain separate from research universe")
    return symbols


def load_scan_universe(
    root: Path, config: AppConfig, *, as_of: date | None = None
) -> tuple[str, ...]:
    """Load the dated dynamic universe, falling back to the checked-in bootstrap list."""
    snapshot = load_universe_snapshot(
        root / "data" / "historical" / "universe", as_of=as_of or date.today()
    )
    if snapshot is not None:
        return _validate_research_symbols(snapshot.symbols, config)

    path = root / "configs" / "us_scan_universe.txt"
    symbols = tuple(
        line.strip().upper()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if len(symbols) != _SCAN_SIZE:
        raise ValueError(f"bootstrap US scan universe must contain exactly {_SCAN_SIZE} symbols")
    _validate_research_symbols(symbols, config)
    if not set(config.market_data.watchlist).issubset(symbols):
        raise ValueError("configured watchlist must be included in the bootstrap US scan universe")
    return symbols


def _usable_scan_quote(quote: StoredQuote) -> bool:
    return (
        quote.is_trade_eligible
        and quote.last_trade_price is not None
        and quote.bid_price is not None
        and quote.ask_price is not None
        and quote.volume is not None
        and quote.open_price is not None
        and quote.high_price is not None
        and quote.low_price is not None
        and quote.last_trade_price > 0
        and quote.open_price > 0
        and quote.ask_price >= quote.bid_price > 0
    )


def _bounded(value: float) -> float:
    return min(1.0, max(0.0, value))


def broad_opportunity_score(
    quote: StoredQuote,
    *,
    max_spread_pct: float,
    metrics: BroadScanMetrics | None = None,
) -> BroadScanScore:
    """Cheap 200->30 opportunity score, separate from the finalist trading score."""
    if not _usable_scan_quote(quote):
        return BroadScanScore(
            quote.symbol, 0.0, {}, False, quote.invalid_reason or "invalid scan quote"
        )
    metrics = metrics or BroadScanMetrics()
    price = float(quote.last_trade_price)
    open_price = float(quote.open_price)
    high = float(quote.high_price)
    low = float(quote.low_price)
    volume = float(quote.volume)
    midpoint = (float(quote.bid_price) + float(quote.ask_price)) / 2
    spread_pct = (float(quote.ask_price) - float(quote.bid_price)) / midpoint
    components = {
        "liquidity": _bounded(math.log10(max(1.0, price * volume)) / 9.0),
        "rvol": _bounded((metrics.rvol if metrics.rvol is not None else 1.0) / 4.0),
        "volume_acceleration": _bounded(
            (metrics.volume_acceleration if metrics.volume_acceleration is not None else 1.0) / 3.0
        ),
        "gap": _bounded(abs(price / open_price - 1.0) / 0.05),
        "range": _bounded(max(0.0, high - low) / open_price / 0.05),
        "spread": 1.0 - _bounded(spread_pct / max_spread_pct),
        "relative_strength": _bounded(
            0.5 + (metrics.relative_strength if metrics.relative_strength is not None else 0.0) * 10
        ),
    }
    score = round(
        0.20 * components["liquidity"]
        + 0.20 * components["rvol"]
        + 0.10 * components["volume_acceleration"]
        + 0.15 * components["gap"]
        + 0.15 * components["range"]
        + 0.15 * components["spread"]
        + 0.05 * components["relative_strength"],
        10,
    )
    return BroadScanScore(quote.symbol, score, components, True, "passed broad scan gates")


def score_scan_quotes(
    quotes: tuple[StoredQuote, ...],
    *,
    config: AppConfig,
    metrics: dict[str, BroadScanMetrics] | None = None,
) -> tuple[BroadScanScore, ...]:
    metrics = metrics or {}
    rows = [
        broad_opportunity_score(
            quote,
            max_spread_pct=config.research_universe.max_spread_pct,
            metrics=metrics.get(quote.symbol.upper()),
        )
        for quote in quotes
    ]
    return tuple(sorted(rows, key=lambda row: (-row.score, row.symbol)))


def select_research_cohort(
    quotes: tuple[StoredQuote, ...],
    *,
    config: AppConfig,
    session_key: str,
    metrics: dict[str, BroadScanMetrics] | None = None,
) -> tuple[CohortResult, tuple[BroadScanScore, ...]]:
    scored = score_scan_quotes(quotes, config=config, metrics=metrics)
    cohort = build_research_cohort(
        [ResearchCandidate(row.symbol, row.score, valid=row.valid) for row in scored],
        session_key=session_key,
        target=config.research.daily_candidate_count,
        core_count=config.research.core_candidate_count,
        boundary_count=config.research.boundary_candidate_count,
    )
    assert isinstance(cohort, CohortResult)
    return cohort, scored


def select_research_symbols(
    quotes: tuple[StoredQuote, ...],
    *,
    config: AppConfig,
    session_key: str,
    metrics: dict[str, BroadScanMetrics] | None = None,
) -> tuple[str, ...]:
    cohort, _ = select_research_cohort(
        quotes, config=config, session_key=session_key, metrics=metrics
    )
    return tuple(member.symbol for member in cohort.members)
