import sqlite3
from datetime import datetime, timezone

import pandas as pd
import pytest

from day_trading_engine.market_data.history import (
    export_quotes_to_parquet,
    read_quote_history,
    write_feature_dataset,
)
from day_trading_engine.simulation.historical import HistoricalReplay


def _seed_database(path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE market_quotes (
            id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            received_at TEXT NOT NULL,
            last_trade_price REAL,
            volume INTEGER,
            bid_price REAL,
            ask_price REAL
        )
        """
    )
    connection.executemany(
        "INSERT INTO market_quotes VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "AMD", "2026-08-24T13:30:00+00:00", 10.0, 100, 9.99, 10.01),
            (2, "AMD", "2026-08-24T13:31:00+00:00", 10.2, 150, 10.19, 10.21),
            (3, "AMD", "2026-08-24T13:32:00+00:00", 99.0, 9999, 98.9, 99.1),
            (4, "NVDA", "2026-08-24T13:30:00+00:00", 20.0, 200, 19.99, 20.01),
        ],
    )
    connection.commit()
    connection.close()


def test_history_export_and_replay_are_point_in_time(tmp_path) -> None:
    database = tmp_path / "trading.db"
    parquet = tmp_path / "parquet"
    _seed_database(database)
    cutoff = datetime(2026, 8, 24, 13, 31, tzinfo=timezone.utc)

    outputs = export_quotes_to_parquet(database, parquet, as_of=cutoff)
    amd = read_quote_history(parquet, "amd")
    replay = HistoricalReplay(amd).replay(previous_close=9.5)
    feature_files = write_feature_dataset(replay[-1].features, parquet, "AMD")

    assert len(outputs) == 2
    assert len(feature_files) == 1
    assert amd["last_trade_price"].tolist() == [10.0, 10.2]
    assert [frame.features.shape[0] for frame in replay] == [1, 2]
    assert replay[0].features["last_trade_price"].tolist() == [10.0]
    assert replay[-1].features["last_trade_price"].tolist() == [10.0, 10.2]
    assert replay[-1].features["calculated_at"].iloc[-1] == pd.Timestamp(cutoff)
    persisted = pd.read_parquet(feature_files[0])
    assert persisted["feature_version"].unique().tolist() == ["m3-v1"]


def test_history_rejects_ambiguous_cutoffs_and_missing_data(tmp_path) -> None:
    database = tmp_path / "trading.db"
    _seed_database(database)

    with pytest.raises(ValueError, match="timezone-aware"):
        export_quotes_to_parquet(database, tmp_path / "parquet", as_of=datetime(2026, 8, 24))
    with pytest.raises(ValueError, match="timezone-aware"):
        read_quote_history(tmp_path / "parquet", "AMD", as_of=datetime(2026, 8, 24))
    with pytest.raises(ValueError, match="missing feature columns"):
        write_feature_dataset(pd.DataFrame({"received_at": []}), tmp_path, "AMD")
    assert read_quote_history(tmp_path / "missing", "AMD").empty


def test_replay_requires_received_timestamp() -> None:
    with pytest.raises(ValueError, match="received_at is required"):
        HistoricalReplay(pd.DataFrame({"last_trade_price": [10.0]}))
