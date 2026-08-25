from __future__ import annotations

from pathlib import Path

import pandas as pd

from day_trading_engine.providers.questrade_history import HistoricalCandle


def candles_to_frame(candles: tuple[HistoricalCandle, ...]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["start", "end", "open", "high", "low", "close", "volume"])

    frame = pd.DataFrame(candle.model_dump() for candle in candles)
    frame["start"] = pd.to_datetime(frame["start"], utc=True, errors="raise")
    frame["end"] = pd.to_datetime(frame["end"], utc=True, errors="raise")
    frame = frame.sort_values("start", kind="stable").reset_index(drop=True)
    if frame["start"].duplicated().any():
        raise ValueError("historical candles contain duplicate start timestamps")
    return frame


def write_candles_to_parquet(
    candles: tuple[HistoricalCandle, ...],
    root: Path,
    *,
    symbol: str,
    interval: str,
) -> list[Path]:
    frame = candles_to_frame(candles)
    if frame.empty:
        return []

    safe_symbol = _partition_value(symbol.upper(), "symbol")
    safe_interval = _partition_value(interval, "interval")
    frame["date"] = frame["start"].dt.date.astype(str)

    outputs: list[Path] = []
    for day, partition in frame.groupby("date", sort=True):
        target = (
            root
            / f"interval={safe_interval}"
            / f"date={day}"
            / f"symbol={safe_symbol}"
            / "candles.parquet"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        partition.drop(columns="date").to_parquet(target, index=False)
        outputs.append(target)
    return outputs


def aggregate_candles(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    required = {"start", "end", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing candle columns: {', '.join(sorted(missing))}")
    if frame.empty:
        return frame.copy()

    ordered = frame.copy()
    ordered["start"] = pd.to_datetime(ordered["start"], utc=True, errors="raise")
    ordered["end"] = pd.to_datetime(ordered["end"], utc=True, errors="raise")
    ordered = ordered.sort_values("start", kind="stable")
    return (
        ordered.set_index("start")
        .resample(f"{minutes}min", label="left", closed="left")
        .agg(
            end=("end", "last"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["close"])
        .reset_index()
    )


def compare_candles(
    actual: pd.DataFrame,
    expected: pd.DataFrame,
    *,
    price_tolerance: float = 1e-9,
) -> list[str]:
    if price_tolerance < 0:
        raise ValueError("price_tolerance must be non-negative")
    if len(actual) != len(expected):
        return [f"row count differs: actual={len(actual)} expected={len(expected)}"]

    mismatches: list[str] = []
    for index, (left, right) in enumerate(zip(actual.itertuples(), expected.itertuples(), strict=True)):
        if pd.Timestamp(left.start) != pd.Timestamp(right.start):
            mismatches.append(f"row {index}: start differs")
            continue
        for field in ("open", "high", "low", "close"):
            if abs(float(getattr(left, field)) - float(getattr(right, field))) > price_tolerance:
                mismatches.append(f"row {index}: {field} differs")
        if int(left.volume) != int(right.volume):
            mismatches.append(f"row {index}: volume differs")
    return mismatches


def _partition_value(value: str, label: str) -> str:
    if not value or any(not (char.isalnum() or char in ".-_") for char in value):
        raise ValueError(f"invalid {label} partition value")
    return value
