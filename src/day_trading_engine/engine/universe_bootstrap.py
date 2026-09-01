from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Protocol
from zoneinfo import ZoneInfo

from day_trading_engine.core.config import AppConfig
from day_trading_engine.engine.universe import (
    UniverseCandidate,
    UniverseProvenance,
    UniverseSnapshot,
    select_research_universe,
    write_universe_snapshot,
)
from day_trading_engine.market_data.backfill import _sessions
from day_trading_engine.market_data.collector import build_default_collector
from day_trading_engine.providers.alpaca_catalog import (
    AlpacaAsset,
    AlpacaCatalogClient,
    AlpacaDailyBar,
)
from day_trading_engine.providers.questrade import Quote, QuoteBatch, SymbolDetail

_EASTERN = ZoneInfo("America/New_York")
_US_EXCHANGES = frozenset({"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS"})
_QUESTRADE_US_EXCHANGES = frozenset({"NYSE", "NASDAQ", "NYSEAM", "ARCA"})
_EXCLUDED_NAME_MARKERS = (
    " PREFERRED ",
    " PREF ",
    " WARRANT ",
    " RIGHT ",
    " UNIT ",
    " DEPOSITARY ",
    " ADR ",
    " ADS ",
    " NOTE ",
    " BOND ",
)


class _AlpacaCatalog(Protocol):
    feed: str

    def list_active_us_assets(self) -> tuple[AlpacaAsset, ...]: ...

    def get_daily_bars(
        self,
        symbols: list[str] | tuple[str, ...],
        *,
        start: date,
        end: date,
        batch_size: int = 200,
    ) -> dict[str, tuple[AlpacaDailyBar, ...]]: ...


class _QuestradeCatalog(Protocol):
    def get_symbol_details(
        self, symbols: list[str], batch_size: int = 50
    ) -> tuple[SymbolDetail, ...]: ...

    def get_quotes(
        self, symbol_ids: list[int], batch_size: int = 50
    ) -> tuple[QuoteBatch, ...]: ...


def _asset_type(asset: AlpacaAsset) -> str | None:
    if asset.status != "active" or not asset.tradable or asset.exchange not in _US_EXCHANGES:
        return None
    name = f" {asset.name.upper()} "
    if any(marker in name for marker in _EXCLUDED_NAME_MARKERS):
        return None
    if " ETF " in name:
        return "approved_etf"
    if " FUND " in name:
        return None
    # ponytail: Alpaca exposes US-equity class, not a common-vs-preferred subtype. The
    # conservative name exclusions above are the ceiling; replace them when the catalog
    # provider exposes an authoritative security subtype.
    return "common_stock"


def _bar_metrics(
    bars: tuple[AlpacaDailyBar, ...], *, expected_sessions: tuple[date, ...]
) -> tuple[float, float, float, float] | None:
    if not bars or not expected_sessions:
        return None
    allowed = set(expected_sessions)
    usable = tuple(
        row
        for row in bars
        if row.session in allowed
        and row.close > 0
        and row.volume > 0
        and row.high >= row.low > 0
    )
    if not usable:
        return None
    coverage = min(1.0, len({row.session for row in usable}) / len(expected_sessions))
    dollar_volume = median(row.close * row.volume for row in usable)
    volatility = median((row.high - row.low) / row.close for row in usable)
    return usable[-1].close, dollar_volume, volatility, coverage


def _quotes_by_id(batches: tuple[QuoteBatch, ...]) -> dict[int, Quote]:
    return {quote.symbolId: quote for batch in batches for quote in batch.quotes}


def _usable_detail(detail: SymbolDetail) -> bool:
    return (
        detail.isTradable
        and detail.isQuotable
        and detail.currency.upper() == "USD"
        and detail.securityType == "Stock"
        and detail.listingExchange.upper() in _QUESTRADE_US_EXCHANGES
        and bool(detail.industrySector and detail.industrySector.strip())
    )


def build_provider_universe(
    root: Path,
    config: AppConfig,
    *,
    as_of: date,
    alpaca: _AlpacaCatalog | None = None,
    questrade: _QuestradeCatalog | None = None,
    observed_on: date | None = None,
) -> tuple[UniverseSnapshot, Path]:
    """Create the current v3.1 universe from live Alpaca + Questrade evidence."""
    observed_on = observed_on or datetime.now(_EASTERN).date()
    if as_of != observed_on:
        raise ValueError(
            "provider universe bootstrap only supports the current US market date; "
            "historical/future effective dates require point-in-time catalog evidence"
        )

    alpaca = alpaca or AlpacaCatalogClient(root=root)
    questrade = questrade or build_default_collector(root, config).client
    assets = tuple(
        asset
        for asset in alpaca.list_active_us_assets()
        if _asset_type(asset) is not None
    )
    if not assets:
        raise ValueError("provider catalog returned no eligible US equities")

    history_end = as_of - timedelta(days=1)
    expected = tuple(_sessions(as_of - timedelta(days=45), history_end)[-20:])
    if not expected:
        raise ValueError("unable to resolve recent US trading sessions for universe bootstrap")
    bars = alpaca.get_daily_bars(
        [asset.symbol for asset in assets], start=expected[0], end=history_end
    )

    measured: list[tuple[AlpacaAsset, str, float, float, float, float]] = []
    universe = config.research_universe
    cash = config.validation.starting_cash_usd
    for asset in assets:
        asset_type = _asset_type(asset)
        metrics = _bar_metrics(bars.get(asset.symbol, ()), expected_sessions=expected)
        if asset_type is None or metrics is None:
            continue
        price, dollar_volume, volatility, coverage = metrics
        if price > cash or coverage < universe.min_coverage_ratio:
            continue
        measured.append((asset, asset_type, price, dollar_volume, volatility, coverage))

    measured.sort(key=lambda row: (-row[3], -row[4], row[0].symbol))
    validation_budget = max(universe.target * 3, 500)
    measured = measured[:validation_budget]
    if len(measured) < universe.target:
        raise ValueError(
            f"provider catalog produced only {len(measured)} cash/coverage-eligible symbols"
        )

    details = questrade.get_symbol_details(
        [row[0].symbol for row in measured],
        batch_size=config.market_data.quote_batch_size,
    )
    details_by_symbol = {
        detail.symbol.upper(): detail for detail in details if _usable_detail(detail)
    }
    resolved = [
        row + (details_by_symbol[row[0].symbol],)
        for row in measured
        if row[0].symbol in details_by_symbol
    ]

    quote_batches = questrade.get_quotes(
        [row[-1].symbolId for row in resolved],
        batch_size=config.market_data.quote_batch_size,
    )
    quotes = _quotes_by_id(quote_batches)
    candidates: list[UniverseCandidate] = []
    for row in resolved:
        asset, asset_type, fallback_price, dollar_volume, volatility, coverage, detail = row
        quote = quotes.get(detail.symbolId)
        if quote is None or quote.isHalted:
            continue
        bid, ask = quote.bidPrice, quote.askPrice
        if bid is None or ask is None or bid <= 0 or ask < bid:
            continue
        midpoint = (bid + ask) / 2
        price = (
            quote.lastTradePrice
            if quote.lastTradePrice is not None and quote.lastTradePrice > 0
            else fallback_price
        )
        candidates.append(
            UniverseCandidate(
                symbol=asset.symbol,
                security_id=f"questrade:{detail.symbolId}",
                exchange=detail.listingExchange.upper(),
                asset_type=asset_type,
                sector=detail.industrySector or "",
                price=float(price),
                median_dollar_volume=float(dollar_volume),
                spread_pct=float((ask - bid) / midpoint),
                volatility=float(volatility),
                coverage_ratio=float(coverage),
                provider_resolvable=True,
                active=True,
                corporate_action_ok=True,
                is_ipo="ipo" in {value.lower() for value in asset.attributes},
                listing_sessions=len(bars.get(asset.symbol, ())),
            )
        )

    received_at = max(
        (batch.meta.received_at for batch in quote_batches),
        default=datetime.now(_EASTERN),
    )
    provenance = UniverseProvenance(
        catalog_provider="alpaca",
        metrics_provider="alpaca",
        metrics_feed=str(alpaca.feed),
        metrics_start=expected[0].isoformat(),
        metrics_end=history_end.isoformat(),
        identity_provider="questrade",
        quote_provider="questrade",
        quote_received_at=received_at.isoformat(),
    )
    snapshot = select_research_universe(
        candidates,
        effective_from=as_of,
        target=universe.target,
        cash_usd=cash,
        max_spread_pct=universe.max_spread_pct,
        min_coverage_ratio=universe.min_coverage_ratio,
        max_sector_fraction=universe.max_sector_fraction,
        ipo_seasoning_sessions=universe.ipo_seasoning_sessions,
        selector_version=universe.selector_version,
        config_version=config.project.plan_version,
        provenance=provenance,
    )
    if len(snapshot.members) != universe.target:
        member_count = len(snapshot.members)
        raise ValueError(
            f"provider bootstrap produced {member_count}/{universe.target} eligible members"
        )
    path = write_universe_snapshot(root / "data" / "historical" / "universe", snapshot)
    return snapshot, path
