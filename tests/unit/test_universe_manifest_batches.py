from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from day_trading_engine.market_data.backfill import write_universe_manifest


def test_universe_manifest_merges_same_date_batches(tmp_path: Path) -> None:
    as_of = date(2026, 1, 5)
    write_universe_manifest(["AAPL"], as_of=as_of, root=tmp_path)
    path = write_universe_manifest(["MSFT"], as_of=as_of, root=tmp_path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["symbols"] == [
        {"symbol": "AAPL", "provider": "alpaca", "provider_symbol_id": None},
        {"symbol": "MSFT", "provider": "alpaca", "provider_symbol_id": None},
    ]


def test_universe_manifest_merges_duplicate_same_symbol_batches(tmp_path: Path) -> None:
    as_of = date(2026, 1, 5)
    write_universe_manifest(["AAPL"], as_of=as_of, root=tmp_path)

    path = write_universe_manifest(["AAPL"], as_of=as_of, root=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["symbols"] == [
        {"symbol": "AAPL", "provider": "alpaca", "provider_symbol_id": None}
    ]
