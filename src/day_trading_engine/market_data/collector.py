from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from day_trading_engine.core.config import AppConfig, load_config
from day_trading_engine.core.paths import project_root
from day_trading_engine.market_data.store import MarketDataStore, StoredQuote
from day_trading_engine.providers.questrade import (
    Market,
    QuestradeClient,
    QuestradeError,
    TokenStore,
)


@dataclass(frozen=True)
class CollectionResult:
    stored: tuple[StoredQuote, ...]
    failed_symbols: tuple[str, ...]


class QuestradeCollector:
    def __init__(
        self,
        client: QuestradeClient,
        store: MarketDataStore,
        *,
        max_latency_ms: int = 5_000,
        quote_batch_size: int = 50,
    ) -> None:
        self.client = client
        self.store = store
        self.max_latency_ms = max_latency_ms
        self.quote_batch_size = quote_batch_size

    def markets(self) -> tuple[Market, ...]:
        return self.client.get_markets()

    def collect(self, symbols: list[str]) -> CollectionResult:
        normalized = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        resolved: list[tuple[str, int]] = []
        failed: list[str] = []

        for symbol in normalized:
            try:
                match = self.client.resolve_symbol(symbol)
            except QuestradeError:
                failed.append(symbol)
                continue
            resolved.append((symbol, match.symbolId))

        quote_ids = [symbol_id for _, symbol_id in resolved]
        batches = self.client.get_quotes(quote_ids, batch_size=self.quote_batch_size)
        stored: list[StoredQuote] = []
        returned_ids: set[int] = set()
        for batch in batches:
            for quote in batch.quotes:
                returned_ids.add(quote.symbolId)
                stored.append(
                    self.store.store_quote(
                        quote,
                        batch.meta,
                        max_latency_ms=self.max_latency_ms,
                    )
                )

        for symbol, symbol_id in resolved:
            if symbol_id not in returned_ids:
                failed.append(symbol)

        return CollectionResult(stored=tuple(stored), failed_symbols=tuple(failed))


def build_default_collector(root: Path, config: AppConfig | None = None) -> QuestradeCollector:
    app_config = config or load_config(root / "configs" / "v1.yaml")
    refresh_token = _load_refresh_token(root)
    token_store = TokenStore(root / "data" / "questrade_tokens.json")
    client = QuestradeClient(refresh_token=refresh_token, token_store=token_store)
    store = MarketDataStore(root / "data" / "trading.db")
    return QuestradeCollector(
        client=client,
        store=store,
        max_latency_ms=app_config.market_data.max_latency_ms,
        quote_batch_size=app_config.market_data.quote_batch_size,
    )


def _load_refresh_token(root: Path) -> str:
    token = os.getenv("QUESTRADE_REFRESH_TOKEN", "").strip()
    if token:
        return token

    env_file = root / ".env"
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "QUESTRADE_REFRESH_TOKEN":
                return value.strip().strip('"').strip("'")
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect one Questrade Level 1 snapshot")
    parser.add_argument("symbols", nargs="*", help="Optional symbols; defaults to configured watchlist")
    parser.add_argument(
        "--markets",
        action="store_true",
        help="Print market hours and snap-quote limits before collecting",
    )
    args = parser.parse_args(argv)

    root = project_root()
    config = load_config(root / "configs" / "v1.yaml")
    collector = build_default_collector(root, config)

    if args.markets:
        for market in collector.markets():
            print(
                f"{market.name}: start={market.startTime} end={market.endTime} "
                f"snapQuotesLimit={market.snapQuotesLimit}"
            )

    symbols = args.symbols or list(config.market_data.watchlist)
    result = collector.collect(symbols)
    for record in result.stored:
        status = "VALID" if record.is_trade_eligible else f"INVALID:{record.invalid_reason}"
        print(
            f"{record.symbol} last={record.last_trade_price} bid={record.bid_price} "
            f"ask={record.ask_price} latency_ms={record.latency_ms} {status}"
        )
    if result.failed_symbols:
        print(f"Failed symbols: {', '.join(result.failed_symbols)}")
    return 0 if result.stored and not result.failed_symbols else 2


if __name__ == "__main__":
    raise SystemExit(main())
