from datetime import date, datetime, timezone

import pandas as pd
import pytest

from day_trading_engine.features.market import MarketCalendar, build_market_features, resample_candles


def samples() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "received_at": pd.date_range("2026-08-24T13:30:00Z", periods=6, freq="min"),
            "last_trade_price": [10.0, 10.2, 10.1, 10.4, 10.5, 99.0],
            "volume": [100, 150, 190, 250, 320, 9999],
            "bid_price": [9.99, 10.19, 10.09, 10.39, 10.49, 98.9],
            "ask_price": [10.01, 10.21, 10.11, 10.41, 10.51, 99.1],
        }
    )


def test_build_features_respects_as_of_and_is_deterministic() -> None:
    cutoff = datetime(2026, 8, 24, 13, 34, tzinfo=timezone.utc)

    first = build_market_features(samples(), as_of=cutoff, previous_close=9.5)
    second = build_market_features(samples(), as_of=cutoff, previous_close=9.5)

    assert len(first) == 5
    assert first["received_at"].max() <= pd.Timestamp(cutoff)
    assert first["last_trade_price"].max() < 99
    assert first["vwap"].iloc[-1] == pytest.approx(10.2863636364)
    assert first["gap_pct"].iloc[0] == pytest.approx((10 / 9.5) - 1)
    assert first["opening_range_high"].iloc[0] == 10.5
    assert first["opening_range_low"].iloc[0] == 10.0
    pd.testing.assert_frame_equal(first, second)


def test_resample_builds_one_and_five_minute_candles_without_future_rows() -> None:
    cutoff = datetime(2026, 8, 24, 13, 34, tzinfo=timezone.utc)

    one_minute = resample_candles(samples(), 1, as_of=cutoff)
    five_minute = resample_candles(samples(), 5, as_of=cutoff)

    assert len(one_minute) == 5
    assert len(five_minute) == 1
    assert five_minute.iloc[0]["open"] == 10.0
    assert five_minute.iloc[0]["close"] == 10.5
    assert five_minute.iloc[0]["volume"] == 320


def test_market_calendar_handles_weekends_and_injected_holidays() -> None:
    calendar = MarketCalendar(frozenset({date(2026, 9, 7)}))

    assert calendar.is_trading_day(date(2026, 8, 24))
    assert not calendar.is_trading_day(date(2026, 8, 23))
    assert not calendar.is_trading_day(date(2026, 9, 7))


def test_feature_input_validation() -> None:
    with pytest.raises(ValueError, match="missing market columns"):
        build_market_features(pd.DataFrame({"received_at": []}), as_of=datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="timezone-aware"):
        build_market_features(samples(), as_of=datetime(2026, 8, 24, 13, 34))
    with pytest.raises(ValueError, match="minutes must be positive"):
        resample_candles(samples(), 0)
