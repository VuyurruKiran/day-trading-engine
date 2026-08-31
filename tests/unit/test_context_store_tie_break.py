from datetime import UTC, datetime
from pathlib import Path

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.context.store import ContextStore
from day_trading_engine.features.context import build_context_scores

NOW = datetime(2026, 8, 25, 14, 0, tzinfo=UTC)


def test_context_store_breaks_canonical_news_ties_deterministically(tmp_path: Path) -> None:
    low = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="same-id",
        title="Shared catalyst",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
        payload={"normalized_score": 0.1},
    )
    high = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="same-id",
        title="Shared catalyst",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
        payload={"normalized_score": 0.9},
    )

    payloads = []
    for name, records in (("forward.db", (high, low)), ("reverse.db", (low, high))):
        with ContextStore(tmp_path / name) as store:
            store.add_many(records)
            payloads.append(store.as_of(NOW)[0].payload)

    assert payloads == [{"normalized_score": 0.1}] * 2


def test_context_scores_break_duplicate_content_ties_deterministically() -> None:
    low = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="same-id",
        title="Shared catalyst",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
        payload={"normalized_score": 0.1},
    )
    high = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="same-id",
        title="Shared catalyst",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
        payload={"normalized_score": 0.9},
    )

    forward = build_context_scores((low, high), symbol="AAPL", cutoff=NOW)
    reverse = build_context_scores((high, low), symbol="AAPL", cutoff=NOW)

    assert forward.news == reverse.news
