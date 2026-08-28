from __future__ import annotations

from pathlib import Path

from day_trading_engine.core.config import AppConfig
from day_trading_engine.engine.cohort import ResearchCandidate, build_research_cohort
from day_trading_engine.market_data.store import StoredQuote

_SCAN_SIZE = 200


def load_scan_universe(root: Path, config: AppConfig) -> tuple[str, ...]:
    """Load and validate the fixed V1 broad discovery pool."""
    path = root / "configs" / "us_scan_universe.txt"
    symbols = tuple(
        line.strip().upper()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if len(symbols) != _SCAN_SIZE:
        raise ValueError(f"US scan universe must contain exactly {_SCAN_SIZE} symbols")
    if len(symbols) != len(set(symbols)):
        raise ValueError("US scan universe cannot contain duplicate symbols")
    if not set(config.market_data.watchlist).issubset(symbols):
        raise ValueError("configured watchlist must be included in the US scan universe")
    return symbols


def _usable_scan_quote(quote: StoredQuote) -> bool:
    """Require the fields needed by the decision feature path before selection."""
    return (
        quote.is_trade_eligible
        and quote.last_trade_price is not None
        and quote.bid_price is not None
        and quote.ask_price is not None
        and quote.volume is not None
    )


def select_research_symbols(
    quotes: tuple[StoredQuote, ...], *, config: AppConfig, session_key: str
) -> tuple[str, ...]:
    """Reduce one live broad scan to the frozen 30-symbol research cohort."""
    cohort = build_research_cohort(
        [
            ResearchCandidate(
                quote.symbol,
                float(quote.volume or 0),
                valid=_usable_scan_quote(quote),
            )
            for quote in quotes
        ],
        session_key=session_key,
        target=config.research.daily_candidate_count,
        core_count=config.research.core_candidate_count,
        boundary_count=config.research.boundary_candidate_count,
    )
    return tuple(member.symbol for member in cohort.members)
