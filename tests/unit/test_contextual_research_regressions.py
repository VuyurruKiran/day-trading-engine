from datetime import UTC, datetime
from hashlib import sha256

import pytest

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.context.store import ContextStore
from day_trading_engine.engine.domain import CandidateDecision, CandidateInput
from day_trading_engine.engine.ranking import RankingWeights, context_score, shortlist
from day_trading_engine.providers.reddit import RedditProvider

NOW = datetime(2026, 8, 28, 16, tzinfo=UTC)


def _candidate(**overrides):
    """Build a valid contextual-ranking candidate with optional overrides."""
    values = {
        "symbol": "AAA",
        "as_of": NOW,
        "price": 50.0,
        "bid": 49.9,
        "ask": 50.1,
        "volume": 100_000,
        "rvol": 2.0,
        "vwap": 49.0,
        "opening_range_high": 49.5,
        "opening_range_low": 48.5,
        "volatility": 0.01,
        "market_score": 0.4,
    }
    values.update(overrides)
    return CandidateInput(**values)


def test_missing_context_weight_moves_to_technical() -> None:
    """Reassign missing optional-context weight to the technical score."""
    candidate = _candidate(news_score=None, social_score=None, fundamental_score=None)
    base = CandidateDecision("AAA", True, 0.8, ("ok",))

    assert context_score(candidate, base, RankingWeights()) == pytest.approx(0.72)


def test_shortlist_allows_one_qualifier_and_keeps_primary_order() -> None:
    """Allow one eligible finalist and preserve its PRIMARY ranking order."""
    rows = [
        (_candidate(symbol="BBB"), CandidateDecision("BBB", False, 1.0, ("risk",))),
        (_candidate(symbol="AAA"), CandidateDecision("AAA", True, 0.8, ("ok",))),
    ]

    result = shortlist(rows, limit=1)

    assert [row[0].symbol for row in result] == ["AAA"]


def test_news_dedupe_key_preserves_existing_database_contract() -> None:
    record = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="1",
        title="AAPL beats estimates!",
        source_at=NOW,
        received_at=NOW,
    )
    expected = sha256(b"2026-08-28:aapl beats estimates").hexdigest()

    assert record.dedupe_key == expected


def test_reddit_provider_requires_cashtag_and_caps_engagement() -> None:
    payload = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "1",
                        "title": "$AAPL breakout",
                        "created_utc": NOW.timestamp() - 10,
                        "score": 99_999,
                        "num_comments": 50_000,
                        "upvote_ratio": 0.9,
                        "permalink": "/r/stocks/1",
                    }
                },
                {
                    "data": {
                        "id": "2",
                        "title": "AAPL without a cashtag is ambiguous",
                        "created_utc": NOW.timestamp() - 10,
                    }
                },
            ]
        }
    }
    provider = RedditProvider(
        "stocks",
        allowed_symbols=("AAPL",),
        engagement_cap=100,
        fetch_json=lambda *_args, **_kwargs: payload,
    )

    records = provider.fetch(NOW)

    assert len(records) == 1
    assert records[0].symbols == ("AAPL",)
    assert records[0].payload["score"] == 100
    assert records[0].payload["num_comments"] == 100
    assert "body" not in records[0].payload


def test_context_store_persists_collection_errors_and_versions(tmp_path) -> None:
    with ContextStore(tmp_path / "context.db") as store:
        store.record_collection(
            run_at=NOW,
            record_count=3,
            errors=("reddit: unavailable",),
            versions={"algorithm": "v1", "schema": "2"},
        )
        row = store._connection.execute(
            "SELECT record_count, errors, versions FROM context_collection_runs"
        ).fetchone()

    assert row is not None
    assert row[0] == 3
    assert "reddit: unavailable" in row[1]
    assert '"algorithm": "v1"' in row[2]
