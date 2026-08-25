from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from day_trading_engine.market_data.backfill import write_universe_manifest


def test_universe_manifest_merges_same_date_batches(tmp_path: Path) -> None:
    as_of = date(2026, 1, 5)
    write_universe_manifest({"AAPL": 1}, as_of=as_of, root=tmp_path)
    path = write_universe_manifest({"MSFT": 2}, as_of=as_of, root=tmp_path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["symbols"] == [
        {"symbol": "AAPL", "symbol_id": 1},
        {"symbol": "MSFT", "symbol_id": 2},
    ]


def test_universe_manifest_rejects_same_date_symbol_id_conflict(tmp_path: Path) -> None:
    as_of = date(2026, 1, 5)
    write_universe_manifest({"AAPL": 1}, as_of=as_of, root=tmp_path)

    with pytest.raises(ValueError, match="conflicting symbol id"):
        write_universe_manifest({"AAPL": 2}, as_of=as_of, root=tmp_path)
