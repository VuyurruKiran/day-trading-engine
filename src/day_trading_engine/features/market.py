from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

FEATURE_VERSION = "m3-v1"
_REQUIRED = {"received_at", "last_trade_price", "volume", "bid_price", "ask_price"}


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
    return frame.sort_values("received_at", kind="stable").reset_index(drop=True)


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

    frame["volume_delta"] = frame["volume"].diff().fillna(frame["volume"]).clip(lower=0)
    candles = (
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
    return candles


def build_market_features(
    samples: pd.DataFrame,
    *,
    as_of: datetime,
    previous_close: float | None = None,
    benchmark_close: pd.Series | None = None,
    ema_span: int = 9,
    opening_range_minutes: int = 5,
    volatility_window: int = 5,
    rvol_window: int = 5,
) -> pd.DataFrame:
    if ema_span <= 0 or opening_range_minutes <= 0 or volatility_window <= 1 or rvol_window <= 0:
        raise ValueError("feature windows must be positive and volatility_window > 1")

    frame = _prepare(samples, as_of)
    if frame.empty:
        return frame

    price = frame["last_trade_price"].astype(float)
    volume_delta = frame["volume"].astype(float).diff().fillna(frame["volume"]).clip(lower=0)
    weighted = price * volume_delta
    cumulative_volume = volume_delta.cumsum()
    frame["vwap"] = weighted.cumsum().div(cumulative_volume.where(cumulative_volume > 0))
    frame["ema"] = price.ewm(span=ema_span, adjust=False).mean()

    session_start = frame["received_at"].iloc[0]
    opening_end = session_start + pd.Timedelta(minutes=opening_range_minutes)
    opening = frame[frame["received_at"] < opening_end]
    frame["opening_range_high"] = opening["last_trade_price"].max()
    frame["opening_range_low"] = opening["last_trade_price"].min()
    frame["gap_pct"] = None if previous_close is None else (price.iloc[0] / previous_close) - 1

    average_volume = volume_delta.rolling(rvol_window, min_periods=1).mean().shift(1)
    frame["rvol"] = volume_delta.div(average_volume.where(average_volume > 0))
    frame["volatility"] = price.pct_change().rolling(volatility_window).std(ddof=0)
    midpoint = (frame["ask_price"] + frame["bid_price"]) / 2
    frame["spread_pct"] = (frame["ask_price"] - frame["bid_price"]).div(midpoint.where(midpoint > 0))

    returns = price.pct_change().fillna(0).add(1).cumprod().sub(1)
    frame["relative_strength"] = returns
    if benchmark_close is not None:
        benchmark = benchmark_close.reindex(frame.index).astype(float)
        benchmark_returns = benchmark.pct_change().fillna(0).add(1).cumprod().sub(1)
        frame["relative_strength"] = returns - benchmark_returns

    frame["feature_version"] = FEATURE_VERSION
    frame["calculated_at"] = pd.Timestamp(as_of).astimezone(timezone.utc)
    return frame
