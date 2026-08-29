from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean

from day_trading_engine.context.models import ContextRecord

CONTEXT_FEATURE_VERSION = "context-v1"


@dataclass(frozen=True, slots=True)
class ContextScores:
    """Normalized point-in-time contextual scores for one symbol."""

    news: float | None
    reddit: float | None
    fundamentals: float | None
    macro: float | None
    evidence_counts: dict[str, int]


def _bounded(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("context score input must be finite")
    return min(1.0, max(0.0, value))


def _direction(value: object) -> float:
    if isinstance(value, str):
        return {"positive": 1.0, "negative": -1.0, "neutral": 0.0}.get(
            value.strip().lower(), 0.0
        )
    try:
        return min(1.0, max(-1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _number(payload: dict[str, object], key: str, default: float) -> float:
    try:
        return _bounded(float(payload.get(key, default)))
    except (TypeError, ValueError):
        return default


def _record_applies(record: ContextRecord, symbol: str) -> bool:
    return not record.symbols or symbol in record.symbols


def _event_score(record: ContextRecord, cutoff: datetime) -> float:
    payload = dict(record.payload)
    if "normalized_score" in payload:
        try:
            return _bounded(float(payload["normalized_score"]))
        except (TypeError, ValueError):
            pass
    age_hours = max(0.0, (cutoff - record.received_at).total_seconds() / 3600)
    recency = 0.5 ** (age_hours / 6.0)
    direction = _direction(payload.get("direction", 0.0))
    strength = (
        _number(payload, "impact", 0.5)
        * _number(payload, "confidence", 0.5)
        * _number(payload, "relevance", 0.5)
        * recency
    )
    return _bounded(0.5 + 0.5 * direction * strength)


def _aggregate(records: list[ContextRecord], cutoff: datetime, *, cap: int = 5) -> float | None:
    if not records:
        return None
    unique = {record.dedupe_key: record for record in records}
    newest = sorted(unique.values(), key=lambda row: row.received_at, reverse=True)[:cap]
    return _bounded(fmean(_event_score(record, cutoff) for record in newest))


def _reddit_payload_score(payload: dict[str, object]) -> float:
    """Normalize Reddit upvote direction and capped engagement into one [0,1] score."""
    if "normalized_score" in payload:
        try:
            return _bounded(float(payload["normalized_score"]))
        except (TypeError, ValueError):
            pass
    if "upvote_ratio" in payload:
        try:
            ratio = _bounded(float(payload["upvote_ratio"]))
            score = max(0.0, float(payload.get("score", 0) or 0))
            comments = max(0.0, float(payload.get("num_comments", 0) or 0))
        except (TypeError, ValueError):
            return 0.5
        engagement = _bounded(math.log1p(score + comments) / math.log1p(1_000))
        return _bounded(0.5 + (ratio - 0.5) * engagement)
    sentiment = _direction(payload.get("sentiment", payload.get("direction", 0.0)))
    attention = _number(payload, "attention", 0.5)
    engagement = _number(payload, "engagement", 0.5)
    uniqueness = _number(payload, "uniqueness", 0.5)
    spam = _number(payload, "spam_confidence", 0.0)
    strength = attention * engagement * uniqueness * (1.0 - spam)
    return _bounded(0.5 + 0.25 * sentiment * strength)


def _reddit_score(records: list[ContextRecord]) -> float | None:
    if not records:
        return None
    return _bounded(fmean(_reddit_payload_score(dict(record.payload)) for record in records[:20]))


def build_context_scores(
    records: list[ContextRecord] | tuple[ContextRecord, ...],
    *,
    symbol: str,
    cutoff: datetime,
) -> ContextScores:
    """Build normalized scores using only evidence known by the cutoff."""
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("context cutoff must be timezone-aware")
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol is required")
    usable = [
        record
        for record in records
        if (
            record.received_at <= cutoff
            and record.source_at <= cutoff
            and _record_applies(record, normalized_symbol)
        )
    ]
    news = [record for record in usable if record.kind == "news"]
    social = [record for record in usable if record.kind == "social"]
    filing = [record for record in usable if record.kind == "filing"]
    macro = [record for record in usable if record.kind == "macro"]
    return ContextScores(
        news=_aggregate(news, cutoff),
        reddit=_reddit_score(social),
        fundamentals=_aggregate(filing, cutoff),
        macro=_aggregate(macro, cutoff),
        evidence_counts={
            "news": len(news),
            "reddit": len(social),
            "fundamentals": len(filing),
            "macro": len(macro),
        },
    )
