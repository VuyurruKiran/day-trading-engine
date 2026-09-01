from datetime import date

from day_trading_engine.engine.universe import UniverseCandidate, select_research_universe
from day_trading_engine.engine.universe_bootstrap import _usable_detail
from day_trading_engine.providers.questrade import SymbolDetail


def test_provider_universe_keeps_missing_sector_as_context_gap() -> None:
    detail = SymbolDetail(
        symbol="TEST",
        symbolId=1,
        listingExchange="NASDAQ",
        securityType="Stock",
        isQuotable=True,
        isTradable=True,
        currency="USD",
        industrySector=None,
    )

    assert _usable_detail(detail)


def test_unknown_sector_does_not_trigger_sector_concentration_cap() -> None:
    candidates = [
        UniverseCandidate(
            symbol=f"T{index}",
            security_id=f"id-{index}",
            exchange="NASDAQ",
            asset_type="common_stock",
            sector="",
            price=10,
            median_dollar_volume=1_000_000 - index,
            spread_pct=0.001,
            volatility=0.02,
            coverage_ratio=1,
        )
        for index in range(3)
    ]

    snapshot = select_research_universe(
        candidates,
        effective_from=date(2026, 9, 1),
        target=3,
        cash_usd=100,
        max_spread_pct=0.02,
        min_coverage_ratio=0.9,
        max_sector_fraction=0.1,
        ipo_seasoning_sessions=20,
        selector_version="test",
        config_version="test",
    )

    assert len(snapshot.members) == 3
    assert {row.sector for row in snapshot.members} == {"UNKNOWN"}
