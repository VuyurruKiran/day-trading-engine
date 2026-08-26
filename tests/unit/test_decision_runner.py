from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from day_trading_engine.core.config import AppConfig, load_config
from day_trading_engine.engine.runner import run_decision
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.providers.questrade import Quote, ResponseMeta
from day_trading_engine.ui.state import ReportStore


def _config(*, decision_time: str = "07:30") -> AppConfig:
    config = load_config(Path("configs/v1.yaml"))
    return config.model_copy(
        update={
            "project": config.project.model_copy(update={"decision_time": decision_time})
        }
    )


def _seed_market(
    store: MarketDataStore,
    *,
    symbol_count: int = 30,
    minutes: int = 7,
    start: datetime = datetime(2026, 8, 25, 13, 30, tzinfo=UTC),
    delay: int = 0,
) -> None:
    for symbol_index in range(symbol_count):
        symbol = f"T{symbol_index:02d}"
        base = 10.0 + symbol_index / 10
        for minute in range(minutes):
            at = start + timedelta(minutes=minute)
            price = base + minute * 0.05
            store.store_quote(
                Quote(
                    symbol=symbol,
                    symbolId=10_000 + symbol_index,
                    bidPrice=price - 0.01,
                    bidSize=100,
                    askPrice=price + 0.01,
                    askSize=100,
                    lastTradePrice=price,
                    volume=100_000 + minute * 10_000,
                    openPrice=base,
                    highPrice=price,
                    lowPrice=base,
                    delay=delay,
                    isHalted=False,
                ),
                ResponseMeta(
                    source_at=at,
                    received_at=at,
                    source_time_origin="http_date",
                    latency_ms=1,
                    rate_limit_remaining=100,
                    rate_limit_reset=None,
                ),
            )


def _stores(tmp_path: Path) -> tuple[MarketDataStore, ReportStore]:
    return (
        MarketDataStore(tmp_path / "trading.db"),
        ReportStore(tmp_path / "decision_state.db"),
    )


def test_runner_builds_and_persists_engine_decision(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(market_store)

    report = run_decision(
        config=config,
        market_store=market_store,
        report_store=report_store,
        created_at=datetime(2026, 8, 25, 13, 37, tzinfo=UTC),
    )

    assert report.payload["engine_generated"] is True
    assert report.payload["universe_size"] == 30
    assert report.payload["cohort_size"] == 30
    assert report.payload["cohort_shortfall"] == 0
    assert 2 <= len(report.payload["finalists"]) <= 5
    assert report.primary_symbol is not None
    assert report.payload["decision"] == "PRIMARY"
    assert report.payload["decision_state"] == "PRIMARY"
    assert report_store.latest() == report


def test_runner_caps_current_universe_at_locked_v1_size(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(market_store, symbol_count=32)

    report = run_decision(
        config=config,
        market_store=market_store,
        report_store=report_store,
        created_at=datetime(2026, 8, 25, 13, 37, tzinfo=UTC),
    )

    assert report.payload["universe_size"] == 30
    assert report.payload["cohort_size"] == 30
    cohort_symbols = {item["symbol"] for item in report.payload["cohort"]}
    assert "T00" not in cohort_symbols
    assert "T01" not in cohort_symbols


def test_runner_rejects_incomplete_locked_universe(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(market_store, symbol_count=29)

    with pytest.raises(RuntimeError, match="decision universe incomplete"):
        run_decision(
            config=config,
            market_store=market_store,
            report_store=report_store,
            created_at=datetime(2026, 8, 25, 13, 37, tzinfo=UTC),
        )


def test_runner_marks_insufficient_samples_as_data_not_ready(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(market_store, minutes=1)

    report = run_decision(
        config=config,
        market_store=market_store,
        report_store=report_store,
        created_at=datetime(2026, 8, 25, 13, 31, tzinfo=UTC),
    )

    assert report.payload["decision"] == "NO TRADE"
    assert report.payload["decision_state"] == "DATA_NOT_READY"
    assert report.payload["cohort_size"] == 0
    assert report.payload["no_trade_reason"] == (
        "decision data not ready: complete current-session inputs unavailable"
    )


def test_runner_rejects_stale_latest_quote(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(market_store)

    with pytest.raises(RuntimeError, match="latest market quotes are stale"):
        run_decision(
            config=config,
            market_store=market_store,
            report_store=report_store,
            created_at=datetime(2026, 8, 25, 13, 42, tzinfo=UTC),
        )


def test_runner_rejects_one_stale_symbol_in_current_universe(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(market_store, minutes=1)
    _seed_market(market_store, symbol_count=29, minutes=7)

    with pytest.raises(RuntimeError, match="T29 quote is stale"):
        run_decision(
            config=config,
            market_store=market_store,
            report_store=report_store,
            created_at=datetime(2026, 8, 25, 13, 37, tzinfo=UTC),
        )


def test_runner_rejects_quotes_outside_regular_session(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(
        market_store,
        start=datetime(2026, 8, 25, 23, 0, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="outside the regular trading session"):
        run_decision(
            config=config,
            market_store=market_store,
            report_store=report_store,
            created_at=datetime(2026, 8, 25, 23, 7, tzinfo=UTC),
        )


def test_runner_rejects_decision_after_market_close(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(market_store)

    with pytest.raises(RuntimeError, match="decision run is outside the regular trading session"):
        run_decision(
            config=config,
            market_store=market_store,
            report_store=report_store,
            created_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        )


def test_runner_rejects_before_configured_decision_time(tmp_path: Path) -> None:
    config = _config(decision_time="10:00")
    market_store, report_store = _stores(tmp_path)
    _seed_market(market_store)

    with pytest.raises(RuntimeError, match="before the configured daily decision time"):
        run_decision(
            config=config,
            market_store=market_store,
            report_store=report_store,
            created_at=datetime(2026, 8, 25, 13, 37, tzinfo=UTC),
        )


def test_runner_rejects_exchange_holiday(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(
        market_store,
        start=datetime(2026, 12, 25, 14, 30, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="outside the regular trading session"):
        run_decision(
            config=config,
            market_store=market_store,
            report_store=report_store,
            created_at=datetime(2026, 12, 25, 14, 37, tzinfo=UTC),
        )


def test_runner_requires_opening_range_coverage(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(
        market_store,
        start=datetime(2026, 8, 25, 15, 30, tzinfo=UTC),
    )

    report = run_decision(
        config=config,
        market_store=market_store,
        report_store=report_store,
        created_at=datetime(2026, 8, 25, 15, 37, tzinfo=UTC),
    )

    assert report.payload["decision_state"] == "DATA_NOT_READY"
    assert report.primary_symbol is None
    assert len(report.payload["input_rejections"]) == 30
    assert {item["reason"] for item in report.payload["input_rejections"]} == {
        "regular-session opening-range coverage is incomplete"
    }


def test_runner_ignores_ineligible_opening_samples(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(market_store, delay=900)
    _seed_market(
        market_store,
        start=datetime(2026, 8, 25, 15, 30, tzinfo=UTC),
    )

    report = run_decision(
        config=config,
        market_store=market_store,
        report_store=report_store,
        created_at=datetime(2026, 8, 25, 15, 37, tzinfo=UTC),
    )

    assert report.payload["decision_state"] == "DATA_NOT_READY"
    assert report.primary_symbol is None
    assert {item["reason"] for item in report.payload["input_rejections"]} == {
        "regular-session opening-range coverage is incomplete"
    }


def test_runner_blocks_primary_when_manual_position_is_open(tmp_path: Path) -> None:
    config = _config()
    market_store, report_store = _stores(tmp_path)
    _seed_market(market_store)

    first = run_decision(
        config=config,
        market_store=market_store,
        report_store=report_store,
        created_at=datetime(2026, 8, 25, 13, 37, tzinfo=UTC),
    )
    report_store.record_execution(
        first.snapshot_id,
        kind="entry",
        at=datetime(2026, 8, 25, 13, 38, tzinfo=UTC),
        price=10.30,
    )

    second = run_decision(
        config=config,
        market_store=market_store,
        report_store=report_store,
        created_at=datetime(2026, 8, 25, 13, 39, tzinfo=UTC),
    )

    assert second.primary_symbol is None
    assert second.payload["decision"] == "NO TRADE"
    assert second.payload["active_position"] is True
    assert second.payload["no_trade_reason"] == "V1 already has an active position"

    report_store.record_execution(
        first.snapshot_id,
        kind="exit",
        at=datetime(2026, 8, 25, 13, 39, tzinfo=UTC),
        price=10.35,
    )
    third = run_decision(
        config=config,
        market_store=market_store,
        report_store=report_store,
        created_at=datetime(2026, 8, 25, 13, 40, tzinfo=UTC),
    )

    assert third.payload["active_position"] is False
    assert third.primary_symbol is not None
    assert third.payload["decision"] == "PRIMARY"
