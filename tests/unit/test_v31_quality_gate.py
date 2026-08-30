from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.engine.runner import _available_cash, _benchmark_return, _market_score
from day_trading_engine.engine.universe import (
    UniverseCandidate,
    load_universe_snapshot,
    select_research_universe,
    write_universe_snapshot,
)
from day_trading_engine.features.context import (
    _bounded,
    _direction,
    _event_score,
    _headline_direction,
    _reddit_payload_score,
    build_context_scores,
)
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.research.outcomes import evaluate_shadow_outcome, load_replay_bars
from day_trading_engine.research.store import ResearchStore
from day_trading_engine.ui.state import ReportStore

NOW = datetime(2026, 8, 28, 16, 0, tzinfo=UTC)


def _candidate(symbol: str, **overrides: object) -> UniverseCandidate:
    values: dict[str, object] = {
        "symbol": symbol,
        "security_id": f"id-{symbol}",
        "exchange": "NASDAQ",
        "asset_type": "common_stock",
        "sector": "TECH",
        "price": 10.0,
        "median_dollar_volume": 10_000_000.0,
        "spread_pct": 0.002,
        "volatility": 0.02,
        "coverage_ratio": 0.95,
    }
    values.update(overrides)
    return UniverseCandidate(**values)  # type: ignore[arg-type]


def _select(candidates: list[UniverseCandidate], *, target: int = 2):
    return select_research_universe(
        candidates,
        effective_from=date(2026, 9, 1),
        target=target,
        cash_usd=100.0,
        max_spread_pct=0.02,
        min_coverage_ratio=0.90,
        max_sector_fraction=1.0,
        ipo_seasoning_sessions=20,
        selector_version="universe-v1",
        config_version="3.1",
    )


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"symbol": " "}, "identity fields"),
        ({"price": float("nan")}, "must be finite"),
        ({"price": 0.0}, "values are invalid"),
        ({"coverage_ratio": 1.1}, "coverage/listing-session"),
    ],
)
def test_universe_candidate_rejects_invalid_catalog_data(overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        _candidate("AAA", **overrides)


def test_universe_eligibility_reasons_are_explicit() -> None:
    candidates = [
        _candidate("INACTIVE", active=False),
        _candidate("UNRES", provider_resolvable=False),
        _candidate("PREF", asset_type="preferred_stock"),
        _candidate("ACTION", corporate_action_ok=False),
        _candidate("NOLIQ", median_dollar_volume=0.0),
        _candidate("WIDE", spread_pct=0.03),
        _candidate("GAPS", coverage_ratio=0.50),
    ]
    snapshot = _select(candidates, target=7)
    assert {row.reason for row in snapshot.exclusions} == {
        "inactive security",
        "live provider cannot resolve symbol",
        "unsupported asset type",
        "unresolved corporate action",
        "insufficient liquidity history",
        "spread quality below universe limit",
        "historical coverage below universe limit",
    }


def test_universe_selector_rejects_invalid_contract_and_identity_conflicts() -> None:
    with pytest.raises(ValueError, match="target and cash"):
        _select([_candidate("AAA")], target=0)
    with pytest.raises(ValueError, match="max_sector_fraction"):
        select_research_universe(
            [_candidate("AAA")],
            effective_from=date(2026, 9, 1),
            target=1,
            cash_usd=100.0,
            max_spread_pct=0.02,
            min_coverage_ratio=0.90,
            max_sector_fraction=0.0,
            ipo_seasoning_sessions=20,
            selector_version="universe-v1",
            config_version="3.1",
        )
    with pytest.raises(ValueError, match="conflicting security identity"):
        _select([_candidate("AAA"), _candidate("AAA", security_id="other")])


def test_universe_records_below_cutoff_and_rejects_tampered_snapshot(tmp_path: Path) -> None:
    snapshot = _select([_candidate("AAA"), _candidate("BBB")], target=1)
    assert any(row.reason == "below active-universe cutoff" for row in snapshot.exclusions)
    path = write_universe_snapshot(tmp_path, snapshot)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["target"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_universe_snapshot(tmp_path, as_of=date(2026, 9, 2))
    assert load_universe_snapshot(tmp_path / "missing", as_of=date(2026, 9, 2)) is None


def _record(kind: str = "news", **overrides: object) -> ContextRecord:
    values: dict[str, object] = {
        "kind": kind,
        "provider": "test",
        "external_id": f"{kind}-1",
        "title": "neutral headline",
        "source_at": NOW - timedelta(minutes=5),
        "received_at": NOW - timedelta(minutes=4),
        "symbols": ("AAPL",),
        "payload": {},
    }
    values.update(overrides)
    return ContextRecord(**values)  # type: ignore[arg-type]


def test_context_normalizers_cover_invalid_and_lexical_inputs() -> None:
    with pytest.raises(ValueError, match="finite"):
        _bounded(float("nan"))
    assert _direction("positive") == 1.0
    assert _direction("unknown") == 0.0
    assert _direction("bad-number") == 0.0
    assert _headline_direction("profits surge") == 1.0
    assert _headline_direction("fraud losses") == -1.0
    assert _headline_direction("ordinary update") is None


def test_context_event_and_reddit_fallbacks_remain_bounded() -> None:
    invalid_normalized = _record(payload={"normalized_score": "bad", "direction": "positive"})
    score = _event_score(invalid_normalized, NOW)
    assert score is not None and 0.5 < score <= 1.0
    assert _event_score(_record(), NOW) is None
    gdelt = _record(provider="gdelt", title="profit surge")
    assert (_event_score(gdelt, NOW) or 0.0) > 0.5

    neutral = _record("social")
    assert _reddit_payload_score(neutral) == 0.5
    malformed = _record(
        "social",
        title="profits surge",
        payload={"upvote_ratio": "bad", "score": 10},
    )
    assert _reddit_payload_score(malformed) == 0.5
    attention = _record(
        "social",
        payload={
            "direction": "negative",
            "attention": 1.0,
            "engagement": 1.0,
            "uniqueness": 1.0,
            "spam_confidence": 0.0,
        },
    )
    assert _reddit_payload_score(attention) < 0.5


def test_context_builder_validates_cutoff_symbol_and_ages_social() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_context_scores([], symbol="AAPL", cutoff=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="symbol is required"):
        build_context_scores([], symbol=" ", cutoff=NOW)
    old_social = _record(
        "social",
        source_at=NOW - timedelta(days=2),
        received_at=NOW - timedelta(days=2),
    )
    global_macro = _record("macro", symbols=(), payload={"direction": "positive"})
    scores = build_context_scores([old_social, global_macro], symbol="AAPL", cutoff=NOW)
    assert scores.reddit is None
    assert scores.macro is not None


def test_research_outcome_validation_and_missing_history_are_explicit(tmp_path: Path) -> None:
    history = tmp_path / "historical"
    assert load_replay_bars(
        history, symbol="AAPL", session="2026-08-28", snapshot_at=NOW
    ) == []
    target = (
        history
        / "interval=OneMinute"
        / "date=2026-08-28"
        / "symbol=AAPL"
        / "candles.parquet"
    )
    target.parent.mkdir(parents=True)
    pd.DataFrame([{"start": NOW, "close": 10.0}]).to_parquet(target, index=False)
    with pytest.raises(ValueError, match="required candle columns"):
        load_replay_bars(history, symbol="AAPL", session="2026-08-28", snapshot_at=NOW)

    plan = {"symbol": "AAPL", "entry": 10.0, "stop": 9.0, "target": 11.0, "quantity": 1}
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_shadow_outcome(plan, [], snapshot_at=NOW.replace(tzinfo=None))
    assert evaluate_shadow_outcome(plan, [], snapshot_at=NOW)["status"] == "unavailable"
    with pytest.raises(ValueError, match="incomplete"):
        evaluate_shadow_outcome({"symbol": "AAPL"}, [object()], snapshot_at=NOW)  # type: ignore[list-item]
    with pytest.raises(ValueError, match="invalid"):
        evaluate_shadow_outcome(
            {**plan, "quantity": 0},
            [object()],  # type: ignore[list-item]
            snapshot_at=NOW,
        )
    with pytest.raises(ValueError, match="stop must be below entry"):
        evaluate_shadow_outcome(
            {**plan, "stop": 10.0},
            [object()],  # type: ignore[list-item]
            snapshot_at=NOW,
        )


def test_research_store_rejects_mutating_outcomes_and_bad_timestamps(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "research")
    rows = [{"symbol": f"S{i:02d}", "session": "2026-08-28"} for i in range(30)]
    store.save_decision_rows("2026-08-28-snap", rows)
    with pytest.raises(ValueError, match="timezone-aware"):
        store.record_outcome(
            "2026-08-28-snap",
            "S00",
            {"status": "complete"},
            recorded_at=NOW.replace(tzinfo=None),
            session="2026-08-28",
        )
    store.record_outcome(
        "2026-08-28-snap",
        "S00",
        {"status": "complete"},
        recorded_at=NOW,
        session="2026-08-28",
    )
    with pytest.raises(ValueError, match="immutable research outcome"):
        store.record_outcome(
            "2026-08-28-snap",
            "S00",
            {"status": "different"},
            recorded_at=NOW,
            session="2026-08-28",
        )
    assert store.outcome_count("2026-08-28-missing", session="2026-08-28") == 0


def test_v31_critical_market_and_cash_helpers_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="finite"):
        _market_score(float("nan"), 0.0)
    with pytest.raises(RuntimeError, match="set is empty"):
        _benchmark_return(
            MarketDataStore(tmp_path / "trading.db"),
            (),
            session_date="2026-08-28",
            cutoff=NOW,
        )

    class DepletedReports:
        def trade_outcome_history(self):
            return [type("Outcome", (), {"realized_pnl": -100.0})()]

    with pytest.raises(RuntimeError, match="depleted"):
        _available_cash(DepletedReports(), 100.0)  # type: ignore[arg-type]

    assert _available_cash(ReportStore(tmp_path / "state.db"), 100.0) == 100.0
