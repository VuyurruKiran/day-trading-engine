from datetime import UTC, datetime, timedelta

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.features.context import build_context_scores

NOW = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)


def test_news_recency_uses_publication_time_not_collection_time() -> None:
    recent = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="recent",
        title="AAPL beats estimates",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
    )
    old = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="old",
        title="AAPL beats estimates",
        source_at=NOW - timedelta(hours=24),
        received_at=NOW,
        symbols=("AAPL",),
    )
    recent_score = build_context_scores([recent], symbol="AAPL", cutoff=NOW).news
    old_score = build_context_scores([old], symbol="AAPL", cutoff=NOW).news
    assert recent_score is not None and old_score is not None
    assert recent_score > old_score > 0.5


def test_reddit_evidence_expires_after_daily_freshness_window() -> None:
    stale = ContextRecord(
        kind="social",
        provider="reddit",
        external_id="stale",
        title="$AAPL breakout",
        source_at=NOW - timedelta(hours=25),
        received_at=NOW - timedelta(hours=1),
        symbols=("AAPL",),
        payload={"normalized_score": 1.0},
    )
    scores = build_context_scores([stale], symbol="AAPL", cutoff=NOW)
    assert scores.reddit is None
    assert scores.evidence_counts["reddit"] == 0
