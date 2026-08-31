from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from day_trading_engine.core.config import load_config
from day_trading_engine.engine import live
from day_trading_engine.engine.universe import load_universe_snapshot
from day_trading_engine.engine.universe_bootstrap import build_provider_universe
from day_trading_engine.market_data.backfill import _sessions
from day_trading_engine.providers.alpaca_catalog import AlpacaAsset, AlpacaDailyBar
from day_trading_engine.providers.questrade import (
    Quote,
    QuoteBatch,
    ResponseMeta,
    SymbolDetail,
)

ROOT = Path(__file__).resolve().parents[2]


class FakeAlpacaCatalog:
    def __init__(self, count: int = 220) -> None:
        self.assets = tuple(
            AlpacaAsset(
                asset_id=f"asset-{index}",
                symbol=f"S{index:03d}",
                name=f"Sample Company {index} Inc",
                exchange="NASDAQ",
                status="active",
                tradable=True,
            )
            for index in range(count)
        )

    def list_active_us_assets(self) -> tuple[AlpacaAsset, ...]:
        return self.assets

    def get_daily_bars(
        self,
        symbols: list[str] | tuple[str, ...],
        *,
        start: date,
        end: date,
        batch_size: int = 200,
    ) -> dict[str, tuple[AlpacaDailyBar, ...]]:
        sessions = _sessions(start, end)
        return {
            symbol: tuple(
                AlpacaDailyBar(
                    session=session,
                    high=10.2,
                    low=9.8,
                    close=10.0,
                    volume=1_000_000 + int(symbol[1:]),
                )
                for session in sessions
            )
            for symbol in symbols
        }


class FakeQuestradeCatalog:
    def __init__(self) -> None:
        self.symbols_by_id: dict[int, str] = {}

    def get_symbol_details(
        self, symbols: list[str], batch_size: int = 50
    ) -> tuple[SymbolDetail, ...]:
        details = []
        for symbol in symbols:
            index = int(symbol[1:])
            symbol_id = index + 1
            self.symbols_by_id[symbol_id] = symbol
            details.append(
                SymbolDetail(
                    symbol=symbol,
                    symbolId=symbol_id,
                    listingExchange="NASDAQ",
                    securityType="Stock",
                    isQuotable=True,
                    isTradable=True,
                    currency="USD",
                    industrySector=f"Sector-{index % 8}",
                )
            )
        return tuple(details)

    def get_quotes(self, symbol_ids: list[int], batch_size: int = 50) -> tuple[QuoteBatch, ...]:
        now = datetime.now(UTC)
        meta = ResponseMeta(now, now, "test", 0, None, None)
        quotes = tuple(
            Quote(
                symbol=self.symbols_by_id[symbol_id],
                symbolId=symbol_id,
                bidPrice=9.99,
                askPrice=10.01,
                lastTradePrice=10.0,
                volume=1_000_000,
            )
            for symbol_id in symbol_ids
        )
        return (QuoteBatch(quotes=quotes, meta=meta),)


def test_provider_bootstrap_writes_deterministic_versioned_200(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    as_of = date(2026, 9, 1)
    alpaca = FakeAlpacaCatalog()
    questrade = FakeQuestradeCatalog()

    first, path = build_provider_universe(
        tmp_path,
        config,
        as_of=as_of,
        alpaca=alpaca,
        questrade=questrade,
    )
    second, second_path = build_provider_universe(
        tmp_path,
        config,
        as_of=as_of,
        alpaca=alpaca,
        questrade=FakeQuestradeCatalog(),
    )
    loaded = load_universe_snapshot(path.parent, as_of=as_of)

    assert path.name.startswith("US-2026-09-")
    assert second_path == path
    assert len(first.members) == 200
    assert first.checksum == second.checksum
    assert first.universe_id == second.universe_id
    assert loaded is not None
    assert loaded.checksum == first.checksum
    assert all(row.security_id.startswith("questrade:") for row in loaded.members)
    assert all(row.sector.startswith("Sector-") for row in loaded.members)


def test_provider_bootstrap_fails_closed_without_200_valid_candidates(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")

    with pytest.raises(ValueError, match="only 199"):
        build_provider_universe(
            tmp_path,
            config,
            as_of=date(2026, 9, 1),
            alpaca=FakeAlpacaCatalog(199),
            questrade=FakeQuestradeCatalog(),
        )

    assert not list((tmp_path / "data" / "historical" / "universe").glob("US-*.json"))


def test_live_load_bootstraps_when_only_legacy_manifest_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe_root = tmp_path / "data" / "historical" / "universe"
    universe_root.mkdir(parents=True)
    (universe_root / "2026-08-28.json").write_text("{}", encoding="utf-8")
    alpaca = FakeAlpacaCatalog()
    questrade = FakeQuestradeCatalog()

    def bootstrap(root: Path, app_config, *, as_of: date, questrade=None):
        return build_provider_universe(
            root,
            app_config,
            as_of=as_of,
            alpaca=alpaca,
            questrade=questrade,
        )

    monkeypatch.setattr(live, "build_provider_universe", bootstrap)

    snapshot, scan, collection = live._load_active_universe(
        tmp_path,
        config,
        date(2026, 9, 1),
        questrade=questrade,
    )

    assert len(snapshot.members) == 200
    assert scan == snapshot.symbols
    assert collection[:200] == scan
    assert collection[-2:] == tuple(config.research_universe.benchmark_symbols)
