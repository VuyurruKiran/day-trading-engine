from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import day_trading_engine.engine.discovery as discovery
from day_trading_engine.context.models import ContextRecord
from day_trading_engine.core.config import load_config
from day_trading_engine.engine.discovery import (
    BroadScanMetrics,
    broad_opportunity_score,
    build_broad_scan_metrics,
)
from day_trading_engine.engine.universe import (
    UniverseCandidate,
    load_universe_snapshot,
    select_research_universe,
    write_universe_snapshot,
)
from day_trading_engine.features.context import (
    build_context_scores,
    normalize_catalyst_evidence,
    normalize_fundamental_risk,
)
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
        "gap_down_penalty",
        "range",
        "spread",
        "relative_strength",
        "premarket",
    }


def test_broad_scan_metrics_use_provider_baselines_and_directional_gap() -> None:
    metrics = build_broad_scan_metrics(
        _quote(),
        average_daily_volume=1_000_000,
        previous_close=9.5,
        prior_volume=900_000,
        benchmark_return=0.01,
        sector_return=0.02,
        premarket_volume=500_000,
        premarket_gap=0.03,
        premarket_range=0.02,
    )

    assert metrics.rvol == pytest.approx(78.0)
    assert metrics.volume_acceleration == pytest.approx(1 / 9)
    assert metrics.market_relative_strength == pytest.approx(1 / 19 - 0.01)
    assert metrics.sector_relative_strength == pytest.approx(1 / 19 - 0.02)
    assert metrics.directional_gap == pytest.approx(1 / 19)
    assert metrics.premarket_volume == 500_000


def test_broad_scan_does_not_reward_gap_down_like_gap_up() -> None:
    up = broad_opportunity_score(
        _quote(), max_spread_pct=0.02, metrics=BroadScanMetrics(directional_gap=0.04)
    )
    down = broad_opportunity_score(
        _quote(), max_spread_pct=0.02, metrics=BroadScanMetrics(directional_gap=-0.04)
    )

    assert up.score > down.score
    assert down.components["gap"] == 0.0
    assert down.components["gap_down_penalty"] > 0.0


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


def test_catalyst_evidence_has_one_structured_schema() -> None:
    record = ContextRecord(
        kind="filing",
        provider="sec",
        external_id="8k-1",
        title="AAPL filing",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
        payload={
            "catalyst_family": "earnings",
            "direction": "negative",
            "magnitude": 0.8,
            "risk_flags": ["dilution"],
        },
    )

    evidence = normalize_catalyst_evidence(record)

    assert evidence["schema_version"] == "catalyst-v1"
    assert evidence["family"] == "EARNINGS"
    assert evidence["direction"] == -1.0
    assert evidence["risk_flags"] == ("DILUTION",)


def test_fundamental_risk_preserves_missing_fields() -> None:
    record = ContextRecord(
        kind="filing",
        provider="sec",
        external_id="10q-1",
        title="AAPL 10-Q",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
        payload={"dilution_risk": 0.8, "risk_flags": "cash_stress"},
    )

    risk = normalize_fundamental_risk(record)

    assert risk["schema_version"] == "fundamental-risk-v1"
    assert risk["cash"] is None
    assert risk["dilution_risk"] == 0.8
    assert risk["risk_flags"] == ("CASH_STRESS",)
