from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pandas as pd

FEATURE_VERSION = "m3-v2"
_REQUIRED = {"received_at", "last_trade_price", "volume", "bid_price", "ask_price"}
_MAX_SINGLE_SAMPLE_MOVE = 0.25


@dataclass(frozen=True)
class MarketCalendar:
    holidays: frozenset[date] = frozenset()

    def is_trading_day(self, value: date) -> bool:
        # Ponytail: explicit holiday injection avoids a new calendar dependency; add an exchange
        # calendar provider when early closes / venue-specific sessions become strategy inputs.
        return value.weekday() < 5 and value not in self.holidays


def _prepare(samples: pd.DataFrame, as_of: datetime | None = None) -> pd.DataFrame:
    missing = _REQUIRED - set(samples.columns)
    if missing:
        raise ValueError(f"missing market columns: {', '.join(sorted(missing))}")

    frame = samples.copy()
    frame["received_at"] = pd.to_datetime(frame["received_at"], utc=True, errors="raise")
    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        if cutoff.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        frame = frame[frame["received_at"] <= cutoff]
    if "is_trade_eligible" in frame.columns:
        frame = frame[frame["is_trade_eligible"].astype(bool)]
    if frame.empty:
        return frame

    if frame["received_at"].duplicated().any():
        raise ValueError("market samples contain duplicate received_at timestamps")
    if frame[["last_trade_price", "volume", "bid_price", "ask_price"]].isna().any().any():
        raise ValueError("eligible market samples must contain complete Level 1 values")
    if (frame[["last_trade_price", "bid_price", "ask_price"]] <= 0).any().any():
        raise ValueError("eligible market prices must be positive")
    if (frame["volume"] < 0).any():
        raise ValueError("eligible market volume must be non-negative")
    if (frame["ask_price"] < frame["bid_price"]).any():
        raise ValueError("eligible market samples cannot contain crossed markets")

    frame = frame.sort_values("received_at", kind="stable").reset_index(drop=True)
    price_moves = frame["last_trade_price"].astype(float).pct_change().abs()
    if price_moves.gt(_MAX_SINGLE_SAMPLE_MOVE).any():
        raise ValueError("market samples contain implausible price discontinuity")
    return frame


def _volume_deltas(volume: pd.Series) -> pd.Series:
    numeric = volume.astype(float)
    deltas = numeric.diff()
    # A cumulative-volume reset starts a new counter. Count the new counter value rather than
    # silently dropping that traded volume.
    deltas = deltas.where(deltas >= 0, numeric)
    if not deltas.empty:
        deltas.iloc[0] = numeric.iloc[0]
    return deltas


def _session_date(frame: pd.DataFrame) -> date:
    dates = frame["received_at"].dt.date
    if dates.nunique() != 1:
        raise ValueError("market features require a single trading session")
    return dates.iloc[0]


def resample_candles(
    samples: pd.DataFrame,
    minutes: int,
    *,
    as_of: datetime | None = None,
) -> pd.DataFrame:
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    frame = _prepare(samples, as_of)
    if frame.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    _session_date(frame)

    frame["volume_delta"] = _volume_deltas(frame["volume"])
    return (
        frame.set_index("received_at")
        .resample(f"{minutes}min", label="left", closed="left")
        .agg(
            open=("last_trade_price", "first"),
            high=("last_trade_price", "max"),
            low=("last_trade_price", "min"),
            close=("last_trade_price", "last"),
            volume=("volume_delta", "sum"),
        )
        .dropna(subset=["close"])
        .reset_index(names="ts")
    )


def _relative_strength(
    frame: pd.DataFrame,
    benchmark_samples: pd.DataFrame | None,
    as_of: datetime,
) -> pd.Series:
    if benchmark_samples is None:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")

    benchmark = _prepare(benchmark_samples, as_of)
    benchmark = benchmark[benchmark["received_at"].dt.date == _session_date(frame)]
    if benchmark.empty:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")

    aligned = pd.merge_asof(
        frame[["received_at"]],
        benchmark[["received_at", "last_trade_price"]].rename(
            columns={"last_trade_price": "benchmark_price"}
        ),
        on="received_at",
        direction="backward",
    )
    if pd.isna(aligned["benchmark_price"].iloc[0]):
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")

    stock_return = (
        frame["last_trade_price"].astype(float).div(frame["last_trade_price"].iloc[0]) - 1
    )
    benchmark_return = (
        aligned["benchmark_price"].astype(float).div(aligned["benchmark_price"].iloc[0]) - 1
    )
    return stock_return - benchmark_return


def build_market_features(
    samples: pd.DataFrame,
    *,
    as_of: datetime,
    previous_close: float | None = None,
    market_samples: pd.DataFrame | None = None,
    sector_samples: pd.DataFrame | None = None,
    ema_span: int = 9,
    opening_range_minutes: int = 5,
    volatility_window: int = 5,
    rvol_window: int = 5,
) -> pd.DataFrame:
    if ema_span <= 0 or opening_range_minutes <= 0 or volatility_window <= 1 or rvol_window <= 0:
        raise ValueError("feature windows must be positive and volatility_window > 1")
    if previous_close is not None and previous_close <= 0:
        raise ValueError("previous_close must be positive")

    frame = _prepare(samples, as_of)
    if frame.empty:
        return frame
    _session_date(frame)

    price = frame["last_trade_price"].astype(float)
    volume_delta = _volume_deltas(frame["volume"])
    weighted = price * volume_delta
    cumulative_volume = volume_delta.cumsum()
    frame["vwap"] = weighted.cumsum().div(cumulative_volume.where(cumulative_volume > 0))
    frame["ema"] = price.ewm(span=ema_span, adjust=False).mean()

    opening_end = frame["received_at"].iloc[0] + timedelta(minutes=opening_range_minutes)
    opening = frame[frame["received_at"] < opening_end]
    frame["opening_range_high"] = opening["last_trade_price"].max()
    frame["opening_range_low"] = opening["last_trade_price"].min()
    frame["gap_pct"] = (
        float("nan") if previous_close is None else (price.iloc[0] / previous_close) - 1
    )

    average_volume = volume_delta.rolling(rvol_window, min_periods=1).mean().shift(1)
    frame["rvol"] = volume_delta.div(average_volume.where(average_volume > 0))
    frame["volatility"] = price.pct_change().rolling(volatility_window).std(ddof=0)
    midpoint = (frame["ask_price"] + frame["bid_price"]) / 2
    frame["spread_pct"] = (frame["ask_price"] - frame["bid_price"]).div(
        midpoint.where(midpoint > 0)
    )
    frame["market_relative_strength"] = _relative_strength(frame, market_samples, as_of)
    frame["sector_relative_strength"] = _relative_strength(frame, sector_samples, as_of)
    frame["feature_version"] = FEATURE_VERSION
    frame["calculated_at"] = pd.Timestamp(as_of).astimezone(UTC)
    return frame
