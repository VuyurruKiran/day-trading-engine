from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import day_trading_engine.engine.discovery as discovery
from day_trading_engine.context.models import ContextRecord
from day_trading_engine.core.config import load_config
from day_trading_engine.engine.discovery import BroadScanMetrics, broad_opportunity_score
from day_trading_engine.engine.universe import (
    UniverseCandidate,
    load_universe_snapshot,
    select_research_universe,
    write_universe_snapshot,
)
from day_trading_engine.features.context import build_context_scores
from day_trading_engine.market_data.store import StoredQuote

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 28, 16, tzinfo=UTC)


def _candidate(symbol: str, *, sector: str = "TECH", price: float = 10.0) -> UniverseCandidate:
    return UniverseCandidate(
        symbol=symbol,
        security_id=f"id-{symbol}",
        exchange="NASDAQ",
        asset_type="common_stock",
        sector=sector,
        price=price,
        median_dollar_volume=10_000_000,
        spread_pct=0.002,
        volatility=0.02,
        coverage_ratio=0.95,
    )


def _quote(symbol: str = "AAPL") -> StoredQuote:
    return StoredQuote(
        symbol=symbol,
        symbol_id=1,
        bid_price=9.99,
        bid_size=100,
        ask_price=10.01,
        ask_size=100,
        last_trade_price=10.0,
        volume=1_000_000,
        open_price=9.8,
        high_price=10.2,
        low_price=9.7,
        delay_seconds=0,
        is_halted=False,
        source_at=NOW.isoformat(),
        received_at=NOW.isoformat(),
        source_time_origin="test",
        latency_ms=1,
        rate_limit_remaining=100,
        rate_limit_reset=None,
        is_trade_eligible=True,
        invalid_reason=None,
        provider="questrade",
    )


def test_v31_config_contract_loads() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    assert config.project.plan_version == "3.2"
    assert config.research_universe.target == 200
    assert config.history.provider == "alpaca"
    assert config.ranking.technical == 0.5
    assert config.ranking.reddit == 0.05


def test_universe_selection_is_versioned_and_sector_bounded(tmp_path: Path) -> None:
    snapshot = select_research_universe(
        [_candidate("AAA"), _candidate("BBB"), _candidate("CCC", sector="FIN")],
        effective_from=date(2026, 8, 1),
        target=2,
        cash_usd=100.0,
        max_spread_pct=0.02,
        min_coverage_ratio=0.90,
        max_sector_fraction=0.50,
        ipo_seasoning_sessions=20,
        selector_version="universe-v1",
        config_version="3.1",
    )
    assert len(snapshot.members) == 2
    assert snapshot.universe_id.startswith("US-2026-08-")
    assert {row.sector for row in snapshot.members} == {"TECH", "FIN"}

    path = write_universe_snapshot(tmp_path, snapshot)
    loaded = load_universe_snapshot(tmp_path, as_of=date(2026, 8, 28))
    assert path.exists()
    assert loaded is not None and loaded.checksum == snapshot.checksum


def test_universe_rejects_unaffordable_and_unseasoned_symbols() -> None:
    ipo = replace(_candidate("IPO"), is_ipo=True, listing_sessions=5)
    expensive = _candidate("BIG", price=101.0)
    snapshot = select_research_universe(
        [ipo, expensive],
        effective_from=date(2026, 8, 1),
        target=2,
        cash_usd=100.0,
        max_spread_pct=0.02,
        min_coverage_ratio=0.90,
        max_sector_fraction=1.0,
        ipo_seasoning_sessions=20,
        selector_version="universe-v1",
        config_version="3.1",
    )
    assert not snapshot.members
    assert {row.reason for row in snapshot.exclusions} == {
        "IPO seasoning period incomplete",
        "price exceeds cash-only universe limit",
    }


def test_dynamic_universe_is_preferred_over_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    symbols = tuple(f"S{i:03d}" for i in range(200))
    monkeypatch.setattr(
        discovery,
        "load_universe_snapshot",
        lambda *a, **k: SimpleNamespace(symbols=symbols),
    )
    assert discovery.load_scan_universe(ROOT, config, as_of=date(2026, 8, 28)) == symbols


def test_broad_scan_uses_current_opportunity_signals() -> None:
    row = broad_opportunity_score(
        _quote(),
        max_spread_pct=0.02,
        metrics=BroadScanMetrics(rvol=2.0, volume_acceleration=1.5, relative_strength=0.02),
    )
    assert row.valid is True
    assert 0 <= row.score <= 1
    assert set(row.components) == {
        "liquidity",
        "rvol",
        "volume_acceleration",
        "gap",
        "range",
        "spread",
        "relative_strength",
    }


def test_context_scores_are_point_in_time_and_optional() -> None:
    news = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="n1",
        title="AAPL catalyst",
        source_at=NOW - timedelta(hours=1),
        received_at=NOW - timedelta(minutes=30),
        symbols=("AAPL",),
        payload={"direction": "positive", "impact": 1, "confidence": 1, "relevance": 1},
    )
    future = ContextRecord(
        kind="social",
        provider="reddit",
        external_id="r1",
        title="$AAPL later",
        source_at=NOW + timedelta(minutes=5),
        received_at=NOW + timedelta(minutes=5),
        symbols=("AAPL",),
        payload={"sentiment": "negative"},
    )
    scores = build_context_scores([news, future], symbol="AAPL", cutoff=NOW)
    assert scores.news is not None and scores.news > 0.5
    assert scores.reddit is None
    assert scores.evidence_counts == {
        "news": 1,
        "reddit": 0,
        "fundamentals": 0,
        "macro": 0,
    }
