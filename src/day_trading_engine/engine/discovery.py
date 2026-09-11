from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from day_trading_engine.core.config import AppConfig
from day_trading_engine.engine.cohort import CohortResult, ResearchCandidate, build_research_cohort
from day_trading_engine.engine.universe import UniverseSnapshot, load_universe_snapshot
from day_trading_engine.market_data.store import StoredQuote

_SCAN_SIZE = 200


@dataclass(frozen=True, slots=True)
class BroadScanMetrics:
    rvol: float | None = None
    volume_acceleration: float | None = None
    relative_strength: float | None = None
    market_relative_strength: float | None = None
    sector_relative_strength: float | None = None
    directional_gap: float | None = None
    premarket_volume: float | None = None
    premarket_gap: float | None = None
    premarket_range: float | None = None


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
    root: Path,
    config: AppConfig,
    *,
    as_of: date | None = None,
    snapshot: UniverseSnapshot | None = None,
) -> tuple[str, ...]:
    """Load the dated dynamic universe, falling back to the checked-in bootstrap list."""
    snapshot = snapshot or load_universe_snapshot(
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


def build_broad_scan_metrics(
    quote: StoredQuote,
    *,
    average_daily_volume: float | None = None,
    previous_close: float | None = None,
    prior_volume: float | None = None,
    benchmark_return: float | None = None,
    sector_return: float | None = None,
    premarket_volume: float | None = None,
    premarket_gap: float | None = None,
    premarket_range: float | None = None,
) -> BroadScanMetrics:
    """Build point-in-time discovery metrics from live quote evidence.

    ``average_daily_volume`` is converted to the expected cumulative volume at the
    five-minute decision boundary.  A missing input stays missing; the scorer's
    neutral fallback is only for optional evidence.
    """
    if quote.volume is None or quote.last_trade_price is None:
        return BroadScanMetrics(
            premarket_volume=premarket_volume,
            premarket_gap=premarket_gap,
            premarket_range=premarket_range,
        )

    rvol = None
    if average_daily_volume is not None and average_daily_volume > 0:
        expected = average_daily_volume * (5 / 390)
        rvol = quote.volume / expected if expected > 0 else None

    acceleration = None
    if prior_volume is not None and prior_volume >= 0:
        increment = max(0.0, quote.volume - prior_volume)
        acceleration = increment / max(1.0, prior_volume)

    stock_return = None
    if previous_close is not None and previous_close > 0:
        stock_return = quote.last_trade_price / previous_close - 1.0
    market_relative = (
        None
        if stock_return is None or benchmark_return is None
        else stock_return - benchmark_return
    )
    sector_relative = (
        None if stock_return is None or sector_return is None else stock_return - sector_return
    )
    relative = (
        None
        if market_relative is None and sector_relative is None
        else sum(value for value in (market_relative, sector_relative) if value is not None)
        / len([value for value in (market_relative, sector_relative) if value is not None])
    )
    gap = None if stock_return is None else stock_return
    return BroadScanMetrics(
        rvol=rvol,
        volume_acceleration=acceleration,
        relative_strength=relative,
        market_relative_strength=market_relative,
        sector_relative_strength=sector_relative,
        directional_gap=gap,
        premarket_volume=premarket_volume,
        premarket_gap=premarket_gap,
        premarket_range=premarket_range,
    )


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
    directional_gap = metrics.directional_gap
    if directional_gap is None:
        directional_gap = price / open_price - 1.0
    gap_up = _bounded(max(0.0, directional_gap) / 0.05)
    gap_down = _bounded(max(0.0, -directional_gap) / 0.05)
    premarket = None
    if metrics.premarket_volume is not None:
        premarket = _bounded(math.log10(max(1.0, metrics.premarket_volume)) / 8.0)
    if metrics.premarket_gap is not None:
        premarket = (
            (premarket or 0.5) * 0.5
            + _bounded(max(0.0, metrics.premarket_gap) / 0.05) * 0.5
        )
    if metrics.premarket_range is not None:
        premarket = (premarket or 0.5) * 0.75 + _bounded(metrics.premarket_range / 0.05) * 0.25
    components = {
        "liquidity": _bounded(math.log10(max(1.0, price * volume)) / 9.0),
        "rvol": _bounded((metrics.rvol if metrics.rvol is not None else 1.0) / 4.0),
        "volume_acceleration": _bounded(
            (metrics.volume_acceleration if metrics.volume_acceleration is not None else 1.0) / 0.03
        ),
        "gap": gap_up,
        "gap_down_penalty": gap_down,
        "range": _bounded(max(0.0, high - low) / open_price / 0.05),
        "spread": 1.0 - _bounded(spread_pct / max_spread_pct),
        "relative_strength": _bounded(
            0.5 + (metrics.relative_strength if metrics.relative_strength is not None else 0.0) * 10
        ),
        "premarket": 0.5 if premarket is None else premarket,
    }
    score = round(
        0.20 * components["liquidity"]
        + 0.20 * components["rvol"]
        + 0.10 * components["volume_acceleration"]
        + 0.12 * components["gap"]
        - 0.08 * components["gap_down_penalty"]
        + 0.15 * components["range"]
        + 0.15 * components["spread"]
        + 0.05 * components["relative_strength"]
        + 0.08 * components["premarket"],
        10,
    )
    return BroadScanScore(
        quote.symbol, _bounded(score), components, True, "passed broad scan gates"
    )


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
