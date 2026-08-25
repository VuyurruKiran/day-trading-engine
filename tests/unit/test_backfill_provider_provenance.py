from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from day_trading_engine.market_data.backfill import backfill_one_minute_history
from day_trading_engine.providers.questrade_history import HistoricalCandle


class HistoryClient:
    feed = "sip"

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.calls = 0

    def get_candles(self, symbol_id: int, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        start = kwargs["start"]
        end = kwargs["end"]
        assert isinstance(start, datetime)
        assert isinstance(end, datetime)
        candles: list[HistoricalCandle] = []
        current = start
        while current < end:
            candles.append(
                HistoricalCandle(
                    start=current,
                    end=current + timedelta(minutes=1),
                    open=10,
                    high=11,
                    low=9,
                    close=10.5,
                    volume=100,
                )
            )
            current += timedelta(minutes=1)
        return SimpleNamespace(candles=tuple(candles))


def test_backfill_refetches_when_provider_changes(tmp_path: Path) -> None:
    first = HistoryClient("questrade")
    backfill_one_minute_history(
        first,
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )

    second = HistoryClient("alpaca")
    manifest = backfill_one_minute_history(
        second,
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert first.calls == 1
    assert second.calls == 1
    assert payload["entries"][0]["provider"] == "alpaca"
    assert payload["entries"][0]["feed"] == "sip"
