from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from day_trading_engine.market_data.historical_candles import (
    aggregate_candles,
    candles_to_frame,
    collect_and_validate_day,
    compare_candles,
    write_candles_to_parquet,
)
from day_trading_engine.providers.questrade import HttpResponse, TokenStore
from day_trading_engine.providers.questrade_history import (
    HistoricalCandle,
    QuestradeHistoryClient,
)


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = deque(responses)
        self.urls: list[str] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        self.urls.append(url)
        return self.responses.popleft()


def response(payload: dict[str, object]) -> HttpResponse:
    return HttpResponse(
        status=200,
        headers={"date": "Mon, 24 Aug 2026 22:00:00 GMT"},
        body=json.dumps(payload).encode(),
    )


def auth_response() -> HttpResponse:
    return response(
        {
            "access_token": "access",
            "refresh_token": "rotated",
            "api_server": "https://api01.iq.questrade.com/",
            "expires_in": 1800,
        }
    )


def candle(
    start: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> dict:
    start_at = datetime.fromisoformat(start)
    return {
        "start": start_at.isoformat(),
        "end": (start_at + timedelta(minutes=1)).isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def test_get_candles_uses_documented_endpoint_and_parameters(tmp_path: Path) -> None:
    transport = FakeTransport(
        [
            auth_response(),
            response({"candles": [candle("2026-08-24T09:30:00-04:00", 10, 10.2, 9.9, 10.1, 100)]}),
        ]
    )
    client = QuestradeHistoryClient(
        "refresh",
        TokenStore(tmp_path / "token.json"),
        transport=transport,
    )

    batch = client.get_candles(
        123,
        start=datetime(2026, 8, 24, 9, 30, tzinfo=UTC),
        end=datetime(2026, 8, 24, 10, 30, tzinfo=UTC),
        interval="OneMinute",
    )

    assert len(batch.candles) == 1
    assert "/v1/markets/candles/123?" in transport.urls[-1]
    assert "interval=OneMinute" in transport.urls[-1]
    assert "startTime=" in transport.urls[-1]
    assert "endTime=" in transport.urls[-1]


def test_get_candles_rejects_invalid_ranges_and_interval(tmp_path: Path) -> None:
    client = QuestradeHistoryClient(
        "refresh",
        TokenStore(tmp_path / "token.json"),
        transport=FakeTransport([]),
    )
    start = datetime(2026, 8, 24, 9, 30, tzinfo=UTC)

    with pytest.raises(ValueError, match="timezone-aware"):
        client.get_candles(1, start=start.replace(tzinfo=None), end=start)
    with pytest.raises(ValueError, match="after start"):
        client.get_candles(1, start=start, end=start)
    with pytest.raises(ValueError, match="unsupported historical interval"):
        client.get_candles(1, start=start, end=start + timedelta(hours=1), interval="SevenMinutes")


def test_historical_candle_rejects_inconsistent_ohlc() -> None:
    with pytest.raises(ValueError, match="OHLC"):
        HistoricalCandle.model_validate(
            candle("2026-08-24T09:30:00-04:00", 10, 9.5, 9.0, 10.1, 100)
        )


def five_one_minute_candles() -> tuple[HistoricalCandle, ...]:
    return tuple(
        HistoricalCandle.model_validate(
            candle(
                f"2026-08-24T09:{30 + minute:02d}:00-04:00",
                10 + minute / 10,
                10.2 + minute / 10,
                9.9 + minute / 10,
                10.1 + minute / 10,
                100 + minute,
            )
        )
        for minute in range(5)
    )


def five_minute_reference() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start": [pd.Timestamp("2026-08-24T13:30:00Z")],
            "end": [pd.Timestamp("2026-08-24T13:35:00Z")],
            "open": [10.0],
            "high": [10.6],
            "low": [9.9],
            "close": [10.5],
            "volume": [510],
        }
    )


def test_five_minute_aggregation_matches_reference() -> None:
    actual = aggregate_candles(candles_to_frame(five_one_minute_candles()), 5)

    assert compare_candles(actual, five_minute_reference()) == []


def test_aggregation_sorts_out_of_order_input() -> None:
    frame = candles_to_frame(five_one_minute_candles())
    shuffled = frame.iloc[[4, 1, 3, 0, 2]].reset_index(drop=True)

    actual = aggregate_candles(shuffled, 5)

    assert compare_candles(actual, five_minute_reference()) == []


def test_missing_minute_is_detected_against_native_five_minute_candle() -> None:
    frame = candles_to_frame(five_one_minute_candles()).drop(index=2)

    actual = aggregate_candles(frame, 5)
    mismatches = compare_candles(actual, five_minute_reference())

    assert "row 0: volume differs" in mismatches


def test_compare_candles_detects_end_boundary_mismatch() -> None:
    actual = five_minute_reference()
    expected = five_minute_reference().copy()
    expected.loc[0, "end"] = pd.Timestamp("2026-08-24T13:34:00Z")

    assert compare_candles(actual, expected) == ["row 0: end differs"]


def test_candle_storage_is_partitioned_and_duplicate_safe(tmp_path: Path) -> None:
    item = HistoricalCandle.model_validate(
        candle("2026-08-24T09:30:00-04:00", 10, 10.2, 9.9, 10.1, 100)
    )
    outputs = write_candles_to_parquet(
        (item,),
        tmp_path,
        symbol="amd",
        interval="OneMinute",
    )

    assert outputs[0].parts[-4:] == (
        "interval=OneMinute",
        "date=2026-08-24",
        "symbol=AMD",
        "candles.parquet",
    )
    with pytest.raises(ValueError, match="duplicate start"):
        candles_to_frame((item, item))

    duplicate_frame = candles_to_frame((item,))
    duplicate_frame = pd.concat([duplicate_frame, duplicate_frame], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate start"):
        aggregate_candles(duplicate_frame, 5)


def test_collect_and_validate_day_compares_questrade_one_and_five_minute_data(
    tmp_path: Path,
) -> None:
    one_minute = [item.model_dump(mode="json") for item in five_one_minute_candles()]
    five_minute = [
        {
            "start": "2026-08-24T09:30:00-04:00",
            "end": "2026-08-24T09:35:00-04:00",
            "open": 10.0,
            "high": 10.6,
            "low": 9.9,
            "close": 10.5,
            "volume": 510,
        }
    ]
    transport = FakeTransport(
        [auth_response(), response({"candles": one_minute}), response({"candles": five_minute})]
    )
    client = QuestradeHistoryClient(
        "refresh",
        TokenStore(tmp_path / "token.json"),
        transport=transport,
    )

    report = collect_and_validate_day(
        client,
        symbol="AMD",
        symbol_id=123,
        start=datetime(2026, 8, 24, 13, 30, tzinfo=UTC),
        end=datetime(2026, 8, 24, 13, 35, tzinfo=UTC),
        root=tmp_path / "history",
    )

    assert report.passed
    assert report.one_minute_rows == 5
    assert report.five_minute_rows == 1
    assert len(report.outputs) == 2
