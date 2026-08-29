from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from day_trading_engine.providers.gdelt import GdeltNewsProvider
from day_trading_engine.providers.reddit import RedditProvider

from .models import ContextRecord


class ContextProvider(Protocol):
    name: str

    def fetch(self, received_at: datetime) -> list[ContextRecord]: ...


@dataclass(frozen=True, slots=True)
class CollectionResult:
    records: tuple[ContextRecord, ...]
    errors: tuple[str, ...]


def collect_context(
    providers: list[ContextProvider] | tuple[ContextProvider, ...],
    *,
    received_at: datetime | None = None,
) -> CollectionResult:
    """Collect provider evidence concurrently while preserving provider order."""
    received_at = received_at or datetime.now(UTC)
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")

    provider_list = tuple(providers)
    if not provider_list:
        return CollectionResult((), ())

    def fetch(provider: ContextProvider) -> tuple[list[ContextRecord], str | None]:
        try:
            return provider.fetch(received_at), None
        except Exception as exc:  # Provider isolation is the degraded-mode boundary.
            return [], f"{provider.name}: {exc}"

    records: list[ContextRecord] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(8, len(provider_list))) as executor:
        for batch, error in executor.map(fetch, provider_list):
            records.extend(batch)
            if error is not None:
                errors.append(error)
    return CollectionResult(tuple(records), tuple(errors))


def _merge_news_associations(records: tuple[ContextRecord, ...]) -> tuple[ContextRecord, ...]:
    """Union ticker associations for news records sharing the legacy dedupe identity."""
    merged: dict[str, ContextRecord] = {}
    ordered_keys: list[str] = []
    passthrough: list[ContextRecord] = []
    for record in records:
        if record.kind != "news":
            passthrough.append(record)
            continue
        key = record.dedupe_key
        current = merged.get(key)
        if current is None:
            merged[key] = record
            ordered_keys.append(key)
            continue
        symbols = tuple(dict.fromkeys((*current.symbols, *record.symbols)))
        current_order = (
            current.received_at,
            current.source_at,
            current.provider,
            current.external_id,
        )
        record_order = (
            record.received_at,
            record.source_at,
            record.provider,
            record.external_id,
        )
        selected = record if record_order > current_order else current
        merged[key] = replace(selected, symbols=symbols)
    return tuple(merged[key] for key in ordered_keys) + tuple(passthrough)


def collect_public_context(
    symbols: tuple[str, ...] | list[str],
    *,
    received_at: datetime | None = None,
) -> CollectionResult:
    """Collect no-secret daily news and Reddit evidence for the frozen cohort."""
    normalized = tuple(
        dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip())
    )
    if not normalized:
        raise ValueError("at least one context symbol is required")
    providers: tuple[ContextProvider, ...] = (
        RedditProvider("stocks", allowed_symbols=normalized),
        *(GdeltNewsProvider(symbol, symbols=(symbol,), max_records=10) for symbol in normalized),
    )
    result = collect_context(providers, received_at=received_at)
    return CollectionResult(_merge_news_associations(result.records), result.errors)
