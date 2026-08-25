from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

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
    received_at = received_at or datetime.now(UTC)
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")

    records: list[ContextRecord] = []
    errors: list[str] = []
    for provider in providers:
        try:
            records.extend(provider.fetch(received_at))
        except Exception as exc:  # Provider isolation is the degraded-mode boundary.
            errors.append(f"{provider.name}: {exc}")
    return CollectionResult(tuple(records), tuple(errors))
