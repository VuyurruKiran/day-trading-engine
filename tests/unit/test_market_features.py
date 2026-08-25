from datetime import UTC, date, datetime

import pandas as pd
import pytest

from day_trading_engine.features.market import (
    FEATURE_VERSION,
    MarketCalendar,
    build_market_features,
    resample_candles,
)


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


def benchmark_samples() -> pd.DataFrame:
    frame = samples()
    frame["last_trade_price"] = [100.0, 100.5, 100.8, 101.0, 101.2, 200.0]
    return frame


def test_build_features_respects_as_of_and_is_deterministic() -> None:
    cutoff = datetime(2026, 8, 24, 13, 34, tzinfo=UTC)

    first = build_market_features(
        samples(),
        as_of=cutoff,
        previous_close=9.5,
        market_samples=benchmark_samples(),
        sector_samples=benchmark_samples(),
    )
    second = build_market_features(
        samples(),
        as_of=cutoff,
        previous_close=9.5,
        market_samples=benchmark_samples(),
        sector_samples=benchmark_samples(),
    )

    assert len(first) == 5
    assert first["received_at"].max() <= pd.Timestamp(cutoff)
    assert first["last_trade_price"].max() < 99
    assert first["vwap"].iloc[-1] == pytest.approx(10.228125)
    assert first["gap_pct"].iloc[0] == pytest.approx((10 / 9.5) - 1)
    assert first["opening_range_high"].iloc[0] == 10.5
    assert first["opening_range_low"].iloc[0] == 10.0
    assert first["market_relative_strength"].iloc[-1] == pytest.approx(0.038)
    assert first["sector_relative_strength"].iloc[-1] == pytest.approx(0.038)
    assert set(first["feature_version"]) == {FEATURE_VERSION}
    pd.testing.assert_frame_equal(first, second)


def test_resample_builds_one_and_five_minute_candles_without_future_rows() -> None:
    cutoff = datetime(2026, 8, 24, 13, 34, tzinfo=UTC)

    one_minute = resample_candles(samples(), 1, as_of=cutoff)
    five_minute = resample_candles(samples(), 5, as_of=cutoff)

    assert len(one_minute) == 5
    assert len(five_minute) == 1
    assert five_minute.iloc[0]["open"] == 10.0
    assert five_minute.iloc[0]["close"] == 10.5
    assert five_minute.iloc[0]["volume"] == 320


def test_ineligible_quotes_are_excluded_before_feature_math() -> None:
    frame = samples().iloc[:3].copy()
    frame["is_trade_eligible"] = [1, 0, 1]

    features = build_market_features(
        frame,
        as_of=datetime(2026, 8, 24, 13, 32, tzinfo=UTC),
    )

    assert features["last_trade_price"].tolist() == [10.0, 10.1]
    assert features["vwap"].iloc[-1] == pytest.approx(10.0473684211)


def test_volume_reset_counts_new_counter_volume() -> None:
    frame = samples().iloc[:3].copy()
    frame["volume"] = [100, 150, 20]

    features = build_market_features(
        frame,
        as_of=datetime(2026, 8, 24, 13, 32, tzinfo=UTC),
    )
    candle = resample_candles(frame, 5).iloc[0]

    expected_vwap = ((10.0 * 100) + (10.2 * 50) + (10.1 * 20)) / 170
    assert features["vwap"].iloc[-1] == pytest.approx(expected_vwap)
    assert candle["volume"] == 170


def test_out_of_order_samples_are_sorted_before_feature_math() -> None:
    ordered = samples().iloc[:5].copy()
    shuffled = ordered.iloc[[3, 0, 4, 1, 2]].reset_index(drop=True)
    cutoff = datetime(2026, 8, 24, 13, 34, tzinfo=UTC)

    expected = build_market_features(ordered, as_of=cutoff)
    actual = build_market_features(shuffled, as_of=cutoff)

    pd.testing.assert_frame_equal(actual, expected)


def test_relative_strength_uses_common_point_in_time_baseline() -> None:
    stock = samples().iloc[1:3].reset_index(drop=True)
    benchmark = benchmark_samples().iloc[:3].reset_index(drop=True)

    features = build_market_features(
        stock,
        as_of=datetime(2026, 8, 24, 13, 32, tzinfo=UTC),
        market_samples=benchmark,
    )

    assert features["market_relative_strength"].iloc[0] == pytest.approx(0.0)
    stock_return = 10.1 / 10.2 - 1
    benchmark_return = 100.8 / 100.5 - 1
    assert features["market_relative_strength"].iloc[-1] == pytest.approx(
        stock_return - benchmark_return
    )


def test_market_calendar_handles_weekends_and_injected_holidays() -> None:
    calendar = MarketCalendar(frozenset({date(2026, 9, 7)}))

    assert calendar.is_trading_day(date(2026, 8, 24))
    assert not calendar.is_trading_day(date(2026, 8, 23))
    assert not calendar.is_trading_day(date(2026, 9, 7))


def test_feature_input_validation() -> None:
    with pytest.raises(ValueError, match="missing market columns"):
        build_market_features(pd.DataFrame({"received_at": []}), as_of=datetime.now(UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        build_market_features(samples(), as_of=datetime(2026, 8, 24, 13, 34))
    with pytest.raises(ValueError, match="previous_close must be positive"):
        build_market_features(samples(), as_of=datetime.now(UTC), previous_close=0)
    with pytest.raises(ValueError, match="minutes must be positive"):
        resample_candles(samples(), 0)

    duplicates = pd.concat([samples().iloc[:1], samples().iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate received_at"):
        build_market_features(duplicates, as_of=datetime(2026, 8, 24, 14, tzinfo=UTC))

    multiple_sessions = pd.concat(
        [samples().iloc[:1], samples().iloc[:1].assign(received_at="2026-08-25T13:30:00Z")],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="single trading session"):
        build_market_features(
            multiple_sessions,
            as_of=datetime(2026, 8, 25, 14, tzinfo=UTC),
        )
