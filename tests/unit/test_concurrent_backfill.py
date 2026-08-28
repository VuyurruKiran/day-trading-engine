from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from day_trading_engine.market_data.concurrent_backfill import (
    backfill_one_minute_history_concurrent,
)
from day_trading_engine.providers.questrade_history import HistoricalCandle


@dataclass(frozen=True)
class _Batch:
    candles: tuple[HistoricalCandle, ...]


class _FakeClient:
    provider = "alpaca"
    feed = "sip"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def get_candles(self, symbol: str, *, start, end, interval: str = "OneMinute") -> _Batch:
        assert interval == "OneMinute"
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.02)
            rows = []
            current = start
            while current < end:
                rows.append(
                    HistoricalCandle(
                        start=current,
                        end=current + timedelta(minutes=1),
                        open=10.0,
                        high=10.0,
                        low=10.0,
                        close=10.0,
                        volume=100,
                    )
                )
                current += timedelta(minutes=1)
            return _Batch(tuple(rows))
        finally:
            with self._lock:
                self.active -= 1


class _RetryClient(_FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def get_candles(self, *args, **kwargs) -> _Batch:
        batch = super().get_candles(*args, **kwargs)
        self.calls += 1
        return _Batch(batch.candles[:-1]) if self.calls == 1 else batch


class _IncompleteClient(_FakeClient):
    def get_candles(self, *args, **kwargs) -> _Batch:
        batch = super().get_candles(*args, **kwargs)
        return _Batch(batch.candles[:-1])


def test_concurrent_backfill_runs_multiple_partitions_in_parallel(tmp_path: Path) -> None:
    client = _FakeClient()
    manifest = backfill_one_minute_history_concurrent(
        client,
        symbols=["MSFT", "AAPL"],
        start=date(2026, 8, 25),
        end=date(2026, 8, 25),
        root=tmp_path,
        workers=2,
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert client.max_active >= 2
    assert payload["coverage"]["current_request_complete"] is True
    assert {entry["status"] for entry in payload["entries"]} == {"complete"}
    assert [entry["key"] for entry in payload["entries"]] == sorted(
        entry["key"] for entry in payload["entries"]
    )


def test_concurrent_backfill_persists_successful_retry(tmp_path: Path) -> None:
    client = _RetryClient()
    manifest = backfill_one_minute_history_concurrent(
        client,
        symbols=["AAPL"],
        start=date(2026, 8, 25),
        end=date(2026, 8, 25),
        root=tmp_path,
        workers=1,
    )

    entry = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    assert client.calls == 2
    assert entry["status"] == "complete"
    assert entry["rows"] == 390


def test_concurrent_backfill_records_incomplete_history_as_gap(tmp_path: Path) -> None:
    manifest = backfill_one_minute_history_concurrent(
        _IncompleteClient(),
        symbols=["AAPL"],
        start=date(2026, 8, 25),
        end=date(2026, 8, 25),
        root=tmp_path,
        workers=1,
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entry = payload["entries"][0]
    assert payload["coverage"]["current_request_complete"] is False
    assert entry["status"] != "complete"
    assert entry["missing_minutes"] == 1
    assert entry["files"] == []


def test_concurrent_backfill_rejects_unsafe_worker_count(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workers must be between 1 and 8"):
        backfill_one_minute_history_concurrent(
            _FakeClient(),
            symbols=["AAPL"],
            start=date(2026, 8, 25),
            end=date(2026, 8, 25),
            root=tmp_path,
            workers=9,
        )
