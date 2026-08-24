from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from day_trading_engine.market_data.store import MarketDataStore, StoredQuote
from day_trading_engine.providers.questrade import QuestradeClient, TokenStore


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

    def collect(self, symbols: list[str]) -> CollectionResult:
        normalized = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        resolved: list[tuple[str, int]] = []
        failed: list[str] = []

        for symbol in normalized:
            try:
                match = self.client.resolve_symbol(symbol)
            except Exception:  # provider failures are isolated per symbol
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


def build_default_collector(project_root: Path) -> QuestradeCollector:
    refresh_token = os.getenv("QUESTRADE_REFRESH_TOKEN", "")
    token_store = TokenStore(project_root / "data" / "questrade_tokens.json")
    client = QuestradeClient(refresh_token=refresh_token, token_store=token_store)
    store = MarketDataStore(project_root / "data" / "trading.db")
    return QuestradeCollector(client=client, store=store)
