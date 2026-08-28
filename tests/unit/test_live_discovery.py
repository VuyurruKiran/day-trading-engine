from pathlib import Path

import pytest

from day_trading_engine.core.config import load_config
from day_trading_engine.engine.discovery import load_scan_universe, select_research_symbols
from day_trading_engine.market_data.store import StoredQuote

ROOT = Path(__file__).resolve().parents[2]


def _quote(
    symbol: str,
    index: int,
    *,
    eligible: bool = True,
    volume: int | None = None,
) -> StoredQuote:
    return StoredQuote(
        symbol=symbol,
        symbol_id=index,
        bid_price=10.0,
        bid_size=100,
        ask_price=10.01,
        ask_size=100,
        last_trade_price=10.0,
        volume=1_000_000 - index if volume is None else volume,
        open_price=10.0,
        high_price=10.1,
        low_price=9.9,
        delay_seconds=0,
        is_halted=False,
        source_at="2026-08-27T14:00:00+00:00",
        received_at="2026-08-27T14:00:00+00:00",
        source_time_origin="test",
        latency_ms=1,
        rate_limit_remaining=100,
        rate_limit_reset=None,
        is_trade_eligible=eligible,
        invalid_reason=None if eligible else "invalid",
    )


def _write_universe(root: Path, symbols: list[str]) -> None:
    """Write a temporary scan universe using the production config path."""
    configs = root / "configs"
    configs.mkdir(parents=True)
    (configs / "us_scan_universe.txt").write_text("\n".join(symbols), encoding="utf-8")


def test_live_scan_reduces_200_validated_symbols_to_30() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe = load_scan_universe(ROOT, config)
    quotes = tuple(
        _quote(symbol, index, eligible=index != 0)
        for index, symbol in enumerate(universe)
    )

    selected = select_research_symbols(quotes, config=config, session_key="2026-08-27")

    assert len(universe) == 200
    assert len(selected) == len(set(selected)) == 30
    assert universe[0] not in selected


def test_live_scan_returns_shortfall_when_only_29_quotes_are_usable() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe = load_scan_universe(ROOT, config)
    quotes = tuple(
        _quote(symbol, index, eligible=index < 29)
        for index, symbol in enumerate(universe)
    )

    selected = select_research_symbols(quotes, config=config, session_key="2026-08-27")

    assert len(selected) == 29


def test_live_scan_rejects_missing_volume_before_selection() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe = load_scan_universe(ROOT, config)
    quotes = tuple(
        _quote(symbol, index, volume=0 if index else None)
        for index, symbol in enumerate(universe[:31])
    )
    first = quotes[0]
    quotes = (
        StoredQuote(**{**first.__dict__, "volume": None}),
        *quotes[1:],
    )

    selected = select_research_symbols(quotes, config=config, session_key="2026-08-27")

    assert first.symbol not in selected
    assert len(selected) == 30


def test_scan_universe_rejects_wrong_size(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    _write_universe(tmp_path, list(config.market_data.watchlist))

    with pytest.raises(ValueError, match="exactly 200"):
        load_scan_universe(tmp_path, config)


def test_scan_universe_rejects_duplicates(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe = list(load_scan_universe(ROOT, config))
    universe[-1] = universe[0]
    _write_universe(tmp_path, universe)

    with pytest.raises(ValueError, match="duplicate"):
        load_scan_universe(tmp_path, config)


def test_scan_universe_requires_configured_watchlist(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe = list(load_scan_universe(ROOT, config))
    missing = config.market_data.watchlist[0]
    universe[universe.index(missing)] = "ZZZZ"
    _write_universe(tmp_path, universe)

    with pytest.raises(ValueError, match="watchlist"):
        load_scan_universe(tmp_path, config)
