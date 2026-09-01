from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from day_trading_engine.core.config import load_config
from day_trading_engine.engine import runner
from day_trading_engine.engine.universe import UniverseSelectionRow, UniverseSnapshot
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.providers.questrade import Quote, ResponseMeta
from day_trading_engine.ui.state import ReportStore

ROOT = Path(__file__).resolve().parents[2]


def _seed(store: MarketDataStore, symbol: str, symbol_id: int, base: float) -> None:
    start = datetime(2026, 8, 25, 13, 30, tzinfo=UTC)
    for minute in range(7):
        at = start + timedelta(minutes=minute)
        price = base + minute * 0.05
        store.store_quote(
            Quote(
                symbol=symbol,
                symbolId=symbol_id,
                bidPrice=price - 0.01,
                askPrice=price + 0.01,
                lastTradePrice=price,
                volume=100_000 + minute * 10_000,
                openPrice=base,
                highPrice=price,
                lowPrice=base,
                delay=0,
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


def test_unknown_sector_is_not_used_as_peer_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    symbols = tuple(f"T{index:02d}" for index in range(30))
    config = config.model_copy(
        update={
            "project": config.project.model_copy(update={"decision_time": "07:30"}),
            "market_data": config.market_data.model_copy(update={"watchlist": symbols}),
        }
    )
    market_store = MarketDataStore(tmp_path / "trading.db")
    report_store = ReportStore(tmp_path / "decision_state.db")
    for index, symbol in enumerate(symbols):
        _seed(market_store, symbol, 10_000 + index, 10 + index / 10)
    _seed(market_store, "SPY", 20_001, 100)
    _seed(market_store, "QQQ", 20_002, 100)

    members = tuple(
        UniverseSelectionRow(
            symbol=symbol,
            security_id=f"id-{index}",
            exchange="NASDAQ",
            asset_type="common_stock",
            sector="UNKNOWN",
            score=1.0,
            included=True,
            reason="test",
        )
        for index, symbol in enumerate(symbols)
    )
    snapshot = UniverseSnapshot(
        universe_id="US-2026-08-test",
        effective_from=date(2026, 8, 25).isoformat(),
        selector_version="test",
        config_version="test",
        target=30,
        members=members,
        exclusions=(),
        created_at=datetime(2026, 8, 25, tzinfo=UTC).isoformat(),
        checksum="test",
    )

    def unexpected_sector_return(*args, **kwargs):
        raise AssertionError("UNKNOWN sector used")

    monkeypatch.setattr(runner, "_sector_return", unexpected_sector_return)

    report = runner.run_decision(
        config=config,
        market_store=market_store,
        report_store=report_store,
        created_at=datetime(2026, 8, 25, 13, 37, tzinfo=UTC),
        universe_snapshot=snapshot,
    )

    assert all(row["features"]["sector_return"] is None for row in report.payload["cohort"])
