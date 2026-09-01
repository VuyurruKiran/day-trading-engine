from day_trading_engine.engine.universe_bootstrap import _usable_detail
from day_trading_engine.providers.questrade import SymbolDetail


def test_provider_universe_rejects_missing_sector_context() -> None:
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

    assert not _usable_detail(detail)
