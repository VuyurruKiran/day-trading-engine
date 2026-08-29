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
    if received_at is not None and (received_at.tzinfo is None or received_at.utcoffset() is None):
        raise ValueError("received_at must be timezone-aware")

    provider_list = tuple(providers)
    if not provider_list:
        return CollectionResult((), ())

    def fetch(provider: ContextProvider) -> tuple[list[ContextRecord], str | None]:
        started_at = received_at or datetime.now(UTC)
        try:
            batch = provider.fetch(started_at)
            if received_at is None:
                completed_at = datetime.now(UTC)
                batch = [replace(record, received_at=completed_at) for record in batch]
            return batch, None
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
    """Union duplicate-news tickers without changing first-seen provider order."""
    output: list[ContextRecord] = []
    positions: dict[str, int] = {}
    for record in records:
        if record.kind != "news":
            output.append(record)
            continue
        key = record.dedupe_key
        position = positions.get(key)
        if position is None:
            positions[key] = len(output)
            output.append(record)
            continue
        current = output[position]
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
        output[position] = replace(selected, symbols=symbols)
    return tuple(output)


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
