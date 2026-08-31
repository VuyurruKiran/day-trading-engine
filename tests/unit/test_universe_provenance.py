import json
from datetime import date
from pathlib import Path

import pytest

from day_trading_engine.engine.universe import (
    UniverseCandidate,
    UniverseProvenance,
    load_universe_snapshot,
    select_research_universe,
    write_universe_snapshot,
)


def test_universe_provider_provenance_is_checksummed(tmp_path: Path) -> None:
    provenance = UniverseProvenance(
        catalog_provider="alpaca",
        metrics_provider="alpaca",
        metrics_feed="sip",
        metrics_start="2026-08-03",
        metrics_end="2026-08-31",
        identity_provider="questrade",
        quote_provider="questrade",
        quote_received_at="2026-09-01T14:00:00+00:00",
    )
    snapshot = select_research_universe(
        [
            UniverseCandidate(
                symbol="TEST",
                security_id="questrade:1",
                exchange="NASDAQ",
                asset_type="common_stock",
                sector="Technology",
                price=10,
                median_dollar_volume=1_000_000,
                spread_pct=0.001,
                volatility=0.02,
                coverage_ratio=1,
            )
        ],
        effective_from=date(2026, 9, 1),
        target=1,
        cash_usd=100,
        max_spread_pct=0.02,
        min_coverage_ratio=0.9,
        max_sector_fraction=1,
        ipo_seasoning_sessions=20,
        selector_version="test",
        config_version="test",
        provenance=provenance,
    )
    path = write_universe_snapshot(tmp_path, snapshot)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["provenance"]["metrics_provider"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_universe_snapshot(tmp_path, as_of=date(2026, 9, 1))
