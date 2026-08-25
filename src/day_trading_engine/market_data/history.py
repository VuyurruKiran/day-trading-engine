from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pandas as pd


def export_quotes_to_parquet(
    database: Path,
    root: Path,
    *,
    as_of: datetime | None = None,
) -> list[Path]:
    if as_of is not None and as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    query = "SELECT * FROM market_quotes"
    parameters: tuple[str, ...] = ()
    if as_of is not None:
        query += " WHERE received_at <= ?"
        parameters = (as_of.isoformat(),)
    query += " ORDER BY received_at, symbol"

    with closing(sqlite3.connect(database)) as connection:
        frame = pd.read_sql_query(query, connection, params=parameters)
    if frame.empty:
        return []

    frame["received_at"] = pd.to_datetime(frame["received_at"], utc=True, errors="raise")
    frame["date"] = frame["received_at"].dt.date.astype(str)
    root.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    for (day, symbol), partition in frame.groupby(["date", "symbol"], sort=True):
        target = root / f"date={day}" / f"symbol={symbol}" / "quotes.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        partition.drop(columns="date").to_parquet(target, index=False)
        outputs.append(target)
    return outputs


def write_feature_dataset(frame: pd.DataFrame, root: Path, symbol: str) -> list[Path]:
    required = {"received_at", "feature_version", "calculated_at"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing feature columns: {', '.join(sorted(missing))}")
    if frame.empty:
        return []

    output = frame.copy()
    output["received_at"] = pd.to_datetime(output["received_at"], utc=True, errors="raise")
    output["date"] = output["received_at"].dt.date.astype(str)
    outputs: list[Path] = []
    for day, partition in output.groupby("date", sort=True):
        target = root / f"date={day}" / f"symbol={symbol.upper()}" / "features.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        partition.drop(columns="date").to_parquet(target, index=False)
        outputs.append(target)
    return outputs


def read_quote_history(root: Path, symbol: str, *, as_of: datetime | None = None) -> pd.DataFrame:
    if as_of is not None and as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    files = sorted(root.glob(f"date=*/symbol={symbol.upper()}/quotes.parquet"))
    if not files:
        return pd.DataFrame()
    frame = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
    frame["received_at"] = pd.to_datetime(frame["received_at"], utc=True, errors="raise")
    if as_of is not None:
        frame = frame[frame["received_at"] <= pd.Timestamp(as_of)]
    return frame.sort_values("received_at", kind="stable").reset_index(drop=True)
