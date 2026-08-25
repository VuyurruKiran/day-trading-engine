from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class Direction(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class StructuredEvent:
    source_hash: str
    entity: str | None
    event_type: str
    direction: Direction
    impact: float
    confidence: float
    affected_sectors: tuple[str, ...]
    model: str
    prompt_version: str

    def __post_init__(self) -> None:
        if not 0 <= self.impact <= 1 or not 0 <= self.confidence <= 1:
            raise ValueError("impact/confidence must be in [0,1]")

    @classmethod
    def from_mapping(
        cls, payload: dict[str, object], *, source_text: str, model: str, prompt_version: str
    ) -> StructuredEvent:
        try:
            direction = Direction(str(payload.get("direction", "uncertain")))
            sectors = tuple(str(x) for x in payload.get("affected_sectors", ()))
            return cls(
                source_hash=content_hash(source_text),
                entity=str(payload["entity"]) if payload.get("entity") else None,
                event_type=str(payload.get("event_type", "unknown")),
                direction=direction,
                impact=float(payload.get("impact", 0.0)),
                confidence=float(payload.get("confidence", 0.0)),
                affected_sectors=sectors,
                model=model,
                prompt_version=prompt_version,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid structured AI event") from exc


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def uncertain_event(text: str, *, model: str, prompt_version: str) -> StructuredEvent:
    return StructuredEvent(
        source_hash=content_hash(text),
        entity=None,
        event_type="unavailable",
        direction=Direction.UNCERTAIN,
        impact=0.0,
        confidence=0.0,
        affected_sectors=(),
        model=model,
        prompt_version=prompt_version,
    )


class EventClassifier:
    """Tiny replaceable classifier boundary; providers may be API or local implementations."""

    def classify(self, text: str) -> dict[str, object]:
        raise NotImplementedError


class ClassificationCache:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str, str], StructuredEvent] = {}

    def get(self, text: str, *, model: str, prompt_version: str) -> StructuredEvent | None:
        return self._events.get((content_hash(text), model, prompt_version))

    def put(self, event: StructuredEvent) -> None:
        self._events[(event.source_hash, event.model, event.prompt_version)] = event


def classify_cached(
    text: str,
    *,
    classifier: EventClassifier,
    cache: ClassificationCache,
    model: str,
    prompt_version: str,
) -> StructuredEvent:
    cached = cache.get(text, model=model, prompt_version=prompt_version)
    if cached is not None:
        return cached
    try:
        event = StructuredEvent.from_mapping(
            classifier.classify(text),
            source_text=text,
            model=model,
            prompt_version=prompt_version,
        )
    except (TimeoutError, ValueError):
        event = uncertain_event(text, model=model, prompt_version=prompt_version)
    cache.put(event)
    return event
