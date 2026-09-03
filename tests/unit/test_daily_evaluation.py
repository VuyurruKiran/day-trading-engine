import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.providers.questrade import Quote, ResponseMeta
from day_trading_engine.research.daily import generate_daily_evaluation
from day_trading_engine.ui.state import ReportStore, SavedReport


def _report() -> SavedReport:
    cohort = []
    for index in range(30):
        symbol = f"S{index:02d}"
        cohort.append(
            {
                "symbol": symbol,
                "cohort_rank": index + 1,
                "rank_score": 1.0 - index / 100,
                "primary": index == 0,
                "finalist": index < 3,
                "features": {"price": 100.0},
                "plan": (
                    {
                        "symbol": symbol,
                        "entry": 100.0,
                        "stop": 95.0,
                        "target": 105.0,
                        "quantity": 1,
                    }
                    if index == 0
                    else None
                ),
            }
        )
    return SavedReport(
        "snap-1",
        datetime(2026, 9, 2, 14, 0, tzinfo=UTC),
        "S00",
        {"session": "2026-09-02", "cohort": cohort},
    )


def _quote(symbol: str, price: float, at: datetime) -> tuple[Quote, ResponseMeta]:
    return (
        Quote(
            symbol=symbol,
            symbolId=1,
            bidPrice=price - 0.05,
            askPrice=price + 0.05,
            lastTradePrice=price,
            volume=100,
            delay=0,
            isHalted=False,
        ),
        ResponseMeta(at, at, "http_date", 0, 100, 60),
    )


def test_daily_evaluation_covers_all_30_and_preserves_shadow_only_status(
    tmp_path: Path,
) -> None:
    report = _report()
    ReportStore(tmp_path / "data" / "decision_state.db").save_once(report)
    market = MarketDataStore(tmp_path / "data" / "trading.db")
    at = datetime(2026, 9, 2, 14, 1, tzinfo=UTC)
    market.store_quote(*_quote("S00", 106.0, at))

    target = generate_daily_evaluation(tmp_path, report)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert len(payload["rows"]) == 30
    first = payload["rows"][0]
    assert first["target_status"] == "observed"
    assert first["shadow_outcome"] is None
    assert first["manual_trade_executed"] is False
    assert payload["ledger_effect"] == "none"
    assert payload["observation_provider"] == "Questrade"

    with pytest.raises(ValueError, match="different data"):
        target.write_text(
            target.read_text(encoding="utf-8").replace("S00", "CHANGED"),
            encoding="utf-8",
        )
        generate_daily_evaluation(tmp_path, report)
