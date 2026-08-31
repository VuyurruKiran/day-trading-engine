from datetime import UTC, datetime, timedelta

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.features.context import build_context_scores

NOW = datetime(2026, 8, 25, 14, 0, tzinfo=UTC)


def test_news_cap_prefers_latest_publications() -> None:
    fresh = ContextRecord(
        kind="news",
        provider="test",
        external_id="fresh",
        title="Fresh catalyst",
        source_at=NOW - timedelta(minutes=1),
        received_at=NOW - timedelta(minutes=1),
        symbols=("AAPL",),
        payload={"normalized_score": 1.0},
    )
    stale = [
        ContextRecord(
            kind="news",
            provider="test",
            external_id=f"stale-{index}",
            title=f"Stale catalyst {index}",
            source_at=NOW - timedelta(days=2),
            received_at=NOW - timedelta(seconds=index),
            symbols=("AAPL",),
            payload={"normalized_score": 0.0},
        )
        for index in range(5)
    ]

    scores = build_context_scores([fresh, *stale], symbol="AAPL", cutoff=NOW)

    assert scores.news is not None and scores.news > 0.55


def test_news_cap_ignores_unscoreable_headlines() -> None:
    catalyst = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="catalyst",
        title="AAPL upgrade",
        source_at=NOW - timedelta(minutes=10),
        received_at=NOW - timedelta(minutes=10),
        symbols=("AAPL",),
    )
    unscoreable = [
        ContextRecord(
            kind="news",
            provider="gdelt",
            external_id=f"neutral-{index}",
            title=f"AAPL conference update {index}",
            source_at=NOW - timedelta(minutes=index + 1),
            received_at=NOW - timedelta(minutes=index + 1),
            symbols=("AAPL",),
        )
        for index in range(5)
    ]

    scores = build_context_scores([catalyst, *unscoreable], symbol="AAPL", cutoff=NOW)

    assert scores.news is not None and scores.news > 0.5
