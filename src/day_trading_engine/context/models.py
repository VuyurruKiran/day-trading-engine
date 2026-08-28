from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal

ContextKind = Literal["news", "filing", "macro", "social"]


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _normalized_title(value: str) -> str:
    return " ".join("".join(char if char.isalnum() else " " for char in value.casefold()).split())


@dataclass(frozen=True, slots=True)
class ContextRecord:
    kind: ContextKind
    provider: str
    external_id: str
    title: str
    source_at: datetime
    received_at: datetime
    symbols: tuple[str, ...] = ()
    url: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.external_id.strip() or not self.title.strip():
            raise ValueError("provider, external_id, and title are required")
        _require_aware(self.source_at, "source_at")
        _require_aware(self.received_at, "received_at")
        normalized_symbols = (
            symbol.strip().upper() for symbol in self.symbols if symbol.strip()
        )
        object.__setattr__(self, "symbols", tuple(dict.fromkeys(normalized_symbols)))
        object.__setattr__(self, "payload", dict(self.payload))

    @property
    def dedupe_key(self) -> str:
        if self.kind in {"news", "social"}:
            title = _normalized_title(self.title) or self.url or self.external_id
            day = self.source_at.astimezone(UTC).date().isoformat()
            basis = f"{self.kind}:{day}:{title}"
        else:
            basis = f"{self.provider}:{self.external_id}"
        return sha256(basis.encode("utf-8")).hexdigest()
