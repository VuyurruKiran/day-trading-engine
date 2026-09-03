from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from day_trading_engine.core.config import (
    AppConfig,
    ExtendedGateThresholds,
)
from day_trading_engine.engine.live import _collect_extended_features
from day_trading_engine.engine.strategy import (
    CandidateSnapshot,
    StrategyPolicy,
    evaluate_baseline,
)
from day_trading_engine.features.extended import (
    ExtendedPhaseMetrics,
    ExtendedSessionFeatures,
    extended_gate_reasons,
    normalize_extended_scores,
    phase_metrics,
)
from day_trading_engine.market_data.backfill import _canonical_schedule
from day_trading_engine.market_data.historical_candles import write_candles_to_parquet
from day_trading_engine.market_data.sessions import (
    SessionPhase,
    SessionSchedule,
    archive_schedule,
    schedule_from_markets,
)
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.providers.questrade import Market, Quote, ResponseMeta
from day_trading_engine.providers.questrade_history import HistoricalCandle
from day_trading_engine.research.cycle import build_extended_activation_report


def _features(symbol: str, *, pre: ExtendedPhaseMetrics | None = None) -> ExtendedSessionFeatures:
    return ExtendedSessionFeatures(
        symbol=symbol,
        premarket_session="2026-09-01",
        prior_postmarket_session="2026-08-31",
        premarket=pre,
        prior_postmarket=None,
        premarket_unavailable_reason=None if pre is not None else "missing",
        postmarket_unavailable_reason="missing",
        premarket_provider="questrade",
        postmarket_provider="alpaca",
        premarket_feed="live",
        postmarket_feed="sip",
        schedule_source="questrade_markets",
    )


def test_canonical_schedule_phases_dst_and_early_close() -> None:
    normal = _canonical_schedule(date(2026, 3, 9))
    assert normal.extended_open.endswith("-04:00")
    premarket = pd.Timestamp("2026-03-09T08:00:00-04:00").to_pydatetime()
    assert normal.phase(premarket) is SessionPhase.PRE_MARKET
    early = _canonical_schedule(date(2026, 11, 27))
    assert early.regular_close.startswith("2026-11-27T13:00:00")
    postmarket = pd.Timestamp("2026-11-27T14:00:00-05:00").to_pydatetime()
    assert early.phase(postmarket) is SessionPhase.POST_MARKET


def test_provider_schedule_is_validated_and_archived(tmp_path: Path) -> None:
    market = Market(
        name="NYSE",
        currency="USD",
        extendedStartTime="2026-09-01T04:00:00-04:00",
        startTime="2026-09-01T09:30:00-04:00",
        endTime="2026-09-01T16:00:00-04:00",
        extendedEndTime="2026-09-01T20:00:00-04:00",
    )
    schedule = schedule_from_markets((market,), session=date(2026, 9, 1))
    path = archive_schedule(schedule, tmp_path)
    assert path.exists()
    assert schedule.source == "questrade_markets"
    with pytest.raises(ValueError, match="no complete"):
        schedule_from_markets((Market(name="NYSE"),), session=date(2026, 9, 1))


def test_provider_schedule_ignores_non_us_markets_and_naive_times() -> None:
    us = Market(
        name="NYSE",
        currency="USD",
        extendedStartTime="2026-11-27T04:00:00-05:00",
        startTime="2026-11-27T09:30:00-05:00",
        endTime="2026-11-27T13:00:00-05:00",
        extendedEndTime="2026-11-27T20:00:00-05:00",
    )
    canada = Market(
        name="TSX",
        currency="CAD",
        extendedStartTime="2026-11-27T07:00:00-05:00",
        startTime="2026-11-27T09:30:00-05:00",
        endTime="2026-11-27T16:00:00-05:00",
        extendedEndTime="2026-11-27T17:00:00-05:00",
    )
    naive = Market(
        name="NASDAQ",
        currency="USD",
        extendedStartTime="2026-11-27T04:00:00",
        startTime="2026-11-27T09:30:00",
        endTime="2026-11-27T16:00:00",
        extendedEndTime="2026-11-27T20:00:00",
    )
    schedule = schedule_from_markets((canada, naive, us), session=date(2026, 11, 27))
    assert schedule.regular_close.startswith("2026-11-27T13:00:00")
    conflict = us.model_copy(update={"name": "NASDAQ", "endTime": "2026-11-27T16:00:00-05:00"})
    with pytest.raises(ValueError, match="schedules disagree"):
        schedule_from_markets((us, conflict), session=date(2026, 11, 27))
    with pytest.raises(ValueError, match="timezone-aware"):
        schedule.phase(datetime(2026, 11, 27, 10))


def test_phase_metrics_and_normalization_are_deterministic() -> None:
    frame = pd.DataFrame(
        {
            "start": ["2026-09-01T08:00:00Z", "2026-09-01T08:01:00Z"],
            "open": [10.0, 10.1],
            "high": [10.2, 10.3],
            "low": [9.9, 10.0],
            "close": [10.1, 10.2],
            "volume": [100, 200],
        }
    )
    metrics = phase_metrics(
        frame, previous_close=9.5, include_gap=True, expected_minutes=330
    )
    assert metrics.active_minutes == 2
    assert metrics.return_pct == pytest.approx(0.02)
    assert metrics.gap_pct == pytest.approx(10.2 / 9.5 - 1)
    assert metrics.high == 10.3
    assert metrics.low == 9.9
    assert metrics.volume == 300
    assert metrics.expected_minutes == 330
    assert metrics.last_observed_at == "2026-09-01T08:01:00+00:00"
    features = {"B": _features("B", pre=metrics), "A": _features("A", pre=metrics)}
    scores, phases = normalize_extended_scores(features)
    assert scores["A"] == scores["B"] == 0.5
    assert phases["A"]["postmarket"] == 0.5


def test_phase_metrics_reject_invalid_extended_observations() -> None:
    columns = ["start", "open", "high", "low", "close", "volume"]
    valid = pd.DataFrame(
        [["2026-09-01T08:00:00Z", 10, 10, 10, 10, 1]], columns=columns
    )
    with pytest.raises(ValueError, match="missing extended"):
        phase_metrics(valid.drop(columns="volume"), previous_close=10, include_gap=True)
    with pytest.raises(ValueError, match="finite and positive"):
        phase_metrics(valid, previous_close=0, include_gap=True)
    with pytest.raises(ValueError, match="at least one"):
        phase_metrics(pd.DataFrame(columns=columns), previous_close=10, include_gap=True)
    with pytest.raises(ValueError, match="duplicate"):
        phase_metrics(pd.concat([valid, valid]), previous_close=10, include_gap=True)
    with pytest.raises(ValueError, match="invalid values"):
        phase_metrics(valid.assign(close=0), previous_close=10, include_gap=True)
    with pytest.raises(ValueError, match="non-negative"):
        phase_metrics(valid.assign(volume=-1), previous_close=10, include_gap=True)


def test_extended_feature_availability_contract_fails_closed() -> None:
    metrics = ExtendedPhaseMetrics(1, 10, 0, 0, 0, 0)
    with pytest.raises(ValueError, match="pre-market availability"):
        ExtendedSessionFeatures(
            **{
                **_features("AAPL").__dict__,
                "premarket_unavailable_reason": None,
            }
        )
    with pytest.raises(ValueError, match="pre-market gap"):
        ExtendedSessionFeatures(
            **{
                **_features("AAPL", pre=metrics).__dict__,
                "premarket": ExtendedPhaseMetrics(1, 10, 0, None, 0, 0),
            }
        )
    with pytest.raises(ValueError, match="post-market availability"):
        ExtendedSessionFeatures(
            **{
                **_features("AAPL").__dict__,
                "prior_postmarket": metrics,
            }
        )


def test_winter_extended_candles_keep_eastern_session_partition(tmp_path: Path) -> None:
    schedule = SessionSchedule(
        session="2026-12-01",
        extended_open="2026-12-01T04:00:00-05:00",
        regular_open="2026-12-01T09:30:00-05:00",
        regular_close="2026-12-01T16:00:00-05:00",
        extended_close="2026-12-01T20:00:00-05:00",
        source="test",
    )
    start = datetime.fromisoformat("2026-12-01T19:30:00-05:00")
    candle = HistoricalCandle(
        start=start,
        end=start + timedelta(minutes=1),
        open=1,
        high=1,
        low=1,
        close=1,
        volume=1,
    )
    paths = write_candles_to_parquet(
        (candle,),
        tmp_path,
        symbol="AAPL",
        interval="OneMinute",
        provider="alpaca",
        feed="sip",
        schedule=schedule,
    )
    assert "date=2026-12-01" in paths[0].as_posix()
    stored = pd.read_parquet(paths[0])
    assert stored.loc[0, "session_date"] == "2026-12-01"
    assert stored.loc[0, "session_phase"] == "POST_MARKET"


def test_postmarket_cannot_leak_into_same_session() -> None:
    with pytest.raises(ValueError, match="must precede"):
        ExtendedSessionFeatures(
            **{
                **_features("AAPL").__dict__,
                "prior_postmarket_session": "2026-09-01",
            }
        )


def test_extended_gates_fail_closed_only_for_required_premarket() -> None:
    thresholds = ExtendedGateThresholds(
        min_pre_active_minutes=1,
        min_pre_dollar_volume=1,
        max_pre_abs_return=1,
        max_pre_abs_gap=1,
        max_pre_range=1,
        max_pre_volatility=1,
        min_post_active_minutes=1,
        min_post_dollar_volume=1,
        max_post_abs_return=1,
        max_post_range=1,
        max_post_volatility=1,
    )
    assert extended_gate_reasons(_features("AAPL"), thresholds) == (
        "required pre-market evidence unavailable",
    )
    liquid = ExtendedPhaseMetrics(2, 1000, 0.01, 0.01, 0.02, 0.01)
    assert extended_gate_reasons(_features("AAPL", pre=liquid), thresholds) == ()


def test_extended_gates_report_every_calibrated_limit() -> None:
    thresholds = ExtendedGateThresholds(
        min_pre_active_minutes=10,
        min_pre_dollar_volume=1000,
        max_pre_abs_return=0.05,
        max_pre_abs_gap=0.05,
        max_pre_range=0.05,
        max_pre_volatility=0.05,
        min_post_active_minutes=10,
        min_post_dollar_volume=1000,
        max_post_abs_return=0.05,
        max_post_range=0.05,
        max_post_volatility=0.05,
    )
    unstable = ExtendedPhaseMetrics(1, 1, 0.2, 0.2, 0.2, 0.2)
    features = ExtendedSessionFeatures(
        **{
            **_features("AAPL", pre=unstable).__dict__,
            "prior_postmarket": unstable,
            "postmarket_unavailable_reason": None,
        }
    )
    assert len(extended_gate_reasons(features, thresholds)) == 9


def test_active_gate_config_requires_approved_evidence() -> None:
    source = Path(__file__).parents[2] / "configs" / "v1.yaml"
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    invalid = copy.deepcopy(payload)
    invalid["extended_hours"]["gate_mode"] = "active"
    with pytest.raises(ValueError, match="manually approved"):
        AppConfig.model_validate(invalid)

    valid = copy.deepcopy(payload)
    valid["extended_hours"]["gate_mode"] = "active"
    valid["extended_hours"]["gate_artifact"] = {
        "version": "extended-gates-v1",
        "approved": True,
        "complete_sessions": 15,
        "coverage_ratio": 0.90,
        "deterministic_replay": True,
        "holdout_consumed": True,
        "forward_confirmed": True,
        "no_expectancy_regression": True,
        "no_drawdown_regression": True,
        "no_hard_risk_regression": True,
        "thresholds": {
            field: 0 for field in ExtendedGateThresholds.model_fields
        },
    }
    assert AppConfig.model_validate(valid).extended_hours.gate_mode == "active"
    valid["extended_hours"]["gate_artifact"]["version"] = ""
    with pytest.raises(ValueError, match="manually approved"):
        AppConfig.model_validate(valid)


def test_technical_blend_and_shadow_gate_behavior() -> None:
    policy = StrategyPolicy(1, 1, 0, 0, 0, 0, 2, extended_score_share=0.20)
    values = {
        "symbol": "AAPL",
        "price": 11,
        "bid": 10.9,
        "ask": 11.1,
        "volume": 100,
        "rvol": 2,
        "volatility": 0.01,
        "vwap": 10,
        "opening_range_high": 10.5,
        "extended_vetoes": ("pre-market instability above limit",),
    }
    low = evaluate_baseline(
        (CandidateSnapshot(**values, extended_score=0, extended_gate_active=False),),
        cash_usd=100,
        active_positions=0,
        policy=policy,
    )
    high = evaluate_baseline(
        (CandidateSnapshot(**values, extended_score=1, extended_gate_active=False),),
        cash_usd=100,
        active_positions=0,
        policy=policy,
    )
    assert high.research[0].score - low.research[0].score == pytest.approx(0.20)
    active = evaluate_baseline(
        (CandidateSnapshot(**values, extended_score=1, extended_gate_active=True),),
        cash_usd=100,
        active_positions=0,
        policy=policy,
    )
    assert active.research[0].eligible is False
    assert active.research[0].reason == "pre-market instability above limit"


def test_live_extended_features_keep_provider_provenance(tmp_path: Path) -> None:
    prior = (
        tmp_path
        / "data/historical/market/provider=alpaca/feed=sip/interval=OneMinute"
        / "date=2026-08-31/symbol=AAPL/candles.parquet"
    )
    prior.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "start": "2026-08-31T19:59:00Z",
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100,
                "session_phase": "REGULAR",
            },
            {
                "start": "2026-08-31T20:00:00Z",
                "open": 10,
                "high": 10.2,
                "low": 10,
                "close": 10.1,
                "volume": 50,
                "session_phase": "POST_MARKET",
            },
        ]
    ).to_parquet(prior, index=False)
    pre_start = datetime.fromisoformat("2026-09-01T08:00:00-04:00")
    candle = HistoricalCandle(
        start=pre_start,
        end=pre_start + timedelta(minutes=1),
        open=10.1,
        high=10.3,
        low=10.1,
        close=10.2,
        volume=100,
    )

    class Collector:
        client = SimpleNamespace(
            get_candles=lambda *args, **kwargs: SimpleNamespace(candles=(candle,))
        )

        def symbol_ids(self, symbols):
            return {"AAPL": 1}

    result = _collect_extended_features(
        Collector(),
        tmp_path,
        ("AAPL",),
        session=date(2026, 9, 1),
        schedule=_canonical_schedule(date(2026, 9, 1)),
    )["AAPL"]
    assert result.premarket_provider == "questrade"
    assert result.postmarket_provider == "alpaca"
    assert result.premarket is not None
    assert result.prior_postmarket is not None


def test_live_quote_schema_uses_eastern_session_date(tmp_path: Path) -> None:
    observed = datetime(2026, 9, 1, 23, 59, tzinfo=UTC)
    record = MarketDataStore(tmp_path / "trading.db").store_quote(
        Quote(
            symbol="AAPL",
            symbolId=1,
            bidPrice=10,
            askPrice=10.1,
            lastTradePrice=10.05,
            lastTradeTime=observed - timedelta(seconds=1),
            volume=100,
            openPrice=10,
            highPrice=10.1,
            lowPrice=10,
        ),
        ResponseMeta(observed, observed, "test", 0, 100, None),
    )
    assert record.session_date == "2026-09-01"
    assert record.session_phase == "POST_MARKET"
    assert record.last_trade_time == "2026-09-01T23:58:59+00:00"


def test_delayed_quote_phase_uses_market_timestamp(tmp_path: Path) -> None:
    received = datetime.fromisoformat("2026-09-01T09:35:00-04:00")
    traded = datetime.fromisoformat("2026-09-01T09:20:00-04:00")
    record = MarketDataStore(tmp_path / "trading.db").store_quote(
        Quote(
            symbol="AAPL",
            symbolId=1,
            bidPrice=10,
            askPrice=10.1,
            lastTradePrice=10.05,
            lastTradeTime=traded,
            volume=100,
            delay=900,
        ),
        ResponseMeta(received, received, "test", 0, 100, None),
    )
    assert record.session_phase == "PRE_MARKET"
    assert record.session_date == "2026-09-01"

    with pytest.raises(ValueError, match="market timestamp"):
        MarketDataStore(tmp_path / "naive.db").store_quote(
            Quote(
                symbol="AAPL",
                symbolId=1,
                bidPrice=10,
                askPrice=10.1,
                lastTradePrice=10.05,
                lastTradeTime=datetime(2026, 9, 1, 9, 20),
                volume=100,
            ),
            ResponseMeta(received, received, "test", 0, 100, None),
        )


def test_activation_report_compares_frozen_regular_and_extended_primaries() -> None:
    candidates = [
        {
            "snapshot_id": "snap",
            "session": "2026-09-01",
            "symbol": f"S{index:02d}",
            "primary": index == 0,
            "regular_only_primary": index == 1,
            "extended_hours": {
                "premarket": {} if index < 27 else None,
                "prior_postmarket": {},
            },
        }
        for index in range(30)
    ]
    outcomes = [
        {
            "snapshot_id": "snap",
            "symbol": symbol,
            "status": "complete",
            "shadow_return": value,
        }
        for symbol, value in (("S00", 0.02), ("S01", -0.01))
    ]
    artifact = build_extended_activation_report(candidates, outcomes)
    assert artifact["complete_sessions"] == 1
    assert artifact["premarket_coverage_ratio"] == 0.9
    assert artifact["decision_changes"] == 1
    assert artifact["regular_only"]["expectancy"] == -0.01
    assert artifact["extended_hours"]["expectancy"] == 0.02
    assert artifact["activation_ready"] is False
