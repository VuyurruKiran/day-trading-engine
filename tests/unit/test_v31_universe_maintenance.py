import json
from datetime import date
from pathlib import Path

import pytest

from day_trading_engine.engine.universe import load_universe_snapshot
from day_trading_engine.engine.universe_ledger import UniverseLedger
from day_trading_engine.ops.maintenance import _rebuild_universe

ROOT = Path(__file__).resolve().parents[2]


def test_local_catalog_rebuild_creates_exact_versioned_200(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "v1.yaml").write_text(
        (ROOT / "configs" / "v1.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    candidates = [
        {
            "symbol": f"S{i:03d}",
            "security_id": f"id-{i}",
            "exchange": "NASDAQ",
            "asset_type": "common_stock",
            "sector": f"SECTOR-{i % 4}",
            "price": 10.0,
            "median_dollar_volume": 10_000_000.0,
            "spread_pct": 0.002,
            "volatility": 0.02,
            "coverage_ratio": 0.95,
        }
        for i in range(200)
    ]
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(candidates), encoding="utf-8")

    path = _rebuild_universe(tmp_path, catalog, date(2026, 9, 1))
    snapshot = load_universe_snapshot(path.parent, as_of=date(2026, 9, 1))

    assert path.exists()
    assert snapshot is not None
    assert len(snapshot.members) == 200
    assert snapshot.universe_id.startswith("US-2026-09-")


def test_universe_ledger_rejects_unknown_delisting(tmp_path: Path) -> None:
    ledger = UniverseLedger(tmp_path / "universe.db")
    with pytest.raises(KeyError, match="missing-security"):
        ledger.record_delisting(
            "missing-security",
            effective_on=date(2026, 9, 1),
            reason="delisted",
        )
