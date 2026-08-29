from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.context.store import ContextStore
from day_trading_engine.engine.domain import CandidateDecision, CandidateInput
from day_trading_engine.engine.ranking import RankingWeights, context_score, rank_all, shortlist
from day_trading_engine.engine.runner import _apply_context_scores
from day_trading_engine.features.context import build_context_scores
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


def test_missing_market_context_is_not_silently_zero() -> None:
    """Treat absent market context as missing so its weight follows fallback policy."""
    candidate = _candidate(
        market_score=None,
        news_score=None,
        social_score=None,
        fundamental_score=None,
    )
    base = CandidateDecision("AAA", True, 0.8, ("ok",))

    assert context_score(candidate, base, RankingWeights()) == pytest.approx(0.8)


def test_shortlist_keeps_two_qualifier_primary_order() -> None:
    """Preserve ranked order while enforcing the Plan v2.2 two-finalist minimum."""
    rows = [
        (_candidate(symbol="BBB"), CandidateDecision("BBB", True, 0.7, ("ok",))),
        (_candidate(symbol="AAA"), CandidateDecision("AAA", True, 0.8, ("ok",))),
    ]

    result = shortlist(rows, limit=2)

    assert [row[0].symbol for row in result] == ["AAA", "BBB"]


def test_news_dedupe_key_preserves_existing_database_contract() -> None:
    """Keep existing news hashes stable across the context upgrade."""
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


def test_social_dedupe_uses_stable_provider_identity() -> None:
    """Keep title edits stable while distinct social post IDs remain distinct."""
    first = ContextRecord(
        kind="social",
        provider="reddit",
        external_id="post-1",
        title="$AAPL breakout",
        source_at=NOW,
        received_at=NOW,
    )
    edited = ContextRecord(
        kind="social",
        provider="reddit",
        external_id="post-1",
        title="$AAPL breakout edited",
        source_at=NOW,
        received_at=NOW,
    )
    distinct = ContextRecord(
        kind="social",
        provider="reddit",
        external_id="post-2",
        title="$AAPL breakout",
        source_at=NOW,
        received_at=NOW,
    )

    assert first.dedupe_key == edited.dedupe_key
    assert first.dedupe_key != distinct.dedupe_key


def test_persisted_context_can_reverse_technical_order(tmp_path) -> None:
    """Feed persisted point-in-time context into production ranking inputs."""
    records = tuple(
        ContextRecord(
            kind=kind,
            provider="test",
            external_id=f"{kind}-bbb",
            title=f"BBB {kind}",
            source_at=NOW - timedelta(minutes=5),
            received_at=NOW - timedelta(minutes=1),
            symbols=("BBB",),
            payload={"normalized_score": 1.0},
        )
        for kind in ("macro", "news", "social", "filing")
    )
    evidence = {"AAA": {}, "BBB": {}}
    with ContextStore(tmp_path / "context.db") as store:
        store.add_many(records)
        candidates = _apply_context_scores(
            [_candidate(symbol="AAA"), _candidate(symbol="BBB")],
            evidence=evidence,
            store=store,
            cutoff=NOW,
        )

    rows = [
        (candidates[0], CandidateDecision("AAA", True, 0.8, ("ok",))),
        (candidates[1], CandidateDecision("BBB", True, 0.7, ("ok",))),
    ]
    ranked = rank_all(rows)

    assert ranked[0][0].symbol == "BBB"
    assert evidence["BBB"]["context"]["evidence_counts"] == {
        "news": 1,
        "reddit": 1,
        "fundamentals": 1,
        "macro": 1,
    }


def test_context_store_excludes_future_source_evidence(tmp_path) -> None:
    """Reject records whose publication time is later than the decision cutoff."""
    future = ContextRecord(
        kind="news",
        provider="test",
        external_id="future",
        title="Future evidence",
        source_at=NOW + timedelta(minutes=5),
        received_at=NOW - timedelta(minutes=1),
        symbols=("AAA",),
        payload={"normalized_score": 1.0},
    )
    with ContextStore(tmp_path / "context.db") as store:
        store.add_many((future,))
        assert store.as_of(NOW) == []


def test_reddit_provider_requires_cashtag_caps_and_preserves_signal() -> None:
    """Filter ambiguous posts, cap engagement, and retain Reddit ranking signal."""
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
    scores = build_context_scores(records, symbol="AAPL", cutoff=NOW)

    assert len(records) == 1
    assert records[0].symbols == ("AAPL",)
    assert records[0].payload["score"] == 100
    assert records[0].payload["num_comments"] == 100
    assert scores.reddit is not None and scores.reddit > 0.5
    assert "body" not in records[0].payload


def test_context_store_persists_collection_errors_and_versions(tmp_path) -> None:
    """Persist context collection status and version metadata."""
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


def test_rank_all_bounds_unscaled_production_technical_scores() -> None:
    """Keep percentage weighting meaningful when the baseline emits scores above one."""
    rows = [
        (
            _candidate(
                symbol="AAA",
                market_score=None,
                news_score=None,
                social_score=None,
                fundamental_score=None,
            ),
            CandidateDecision("AAA", True, 12.0, ("ok",)),
        ),
        (
            _candidate(
                symbol="BBB",
                market_score=None,
                news_score=None,
                social_score=None,
                fundamental_score=None,
            ),
            CandidateDecision("BBB", True, 0.8, ("ok",)),
        ),
    ]

    ranked = rank_all(rows)

    assert ranked[0][2] == pytest.approx(1.0)
    assert ranked[1][2] == pytest.approx(0.8)


def test_real_gdelt_shape_is_scored_and_metadata_only_context_is_unavailable() -> None:
    """Use real provider shapes without fabricating neutral SEC/FRED scores."""
    records = [
        ContextRecord(
            kind="news",
            provider="gdelt",
            external_id="news-1",
            title="AAPL beats estimates on strong growth",
            source_at=NOW - timedelta(minutes=5),
            received_at=NOW - timedelta(minutes=1),
            symbols=("AAPL",),
            payload={"domain": "example.com", "language": "English"},
        ),
        ContextRecord(
            kind="filing",
            provider="sec",
            external_id="filing-1",
            title="10-Q filing",
            source_at=NOW - timedelta(minutes=5),
            received_at=NOW - timedelta(minutes=1),
            symbols=("AAPL",),
            payload={"form": "10-Q", "filing_date": "2026-08-28"},
        ),
        ContextRecord(
            kind="macro",
            provider="fred",
            external_id="macro-1",
            title="DFF",
            source_at=NOW - timedelta(minutes=5),
            received_at=NOW - timedelta(minutes=1),
            payload={"series_id": "DFF", "value": "5.25"},
        ),
    ]

    scores = build_context_scores(records, symbol="AAPL", cutoff=NOW)

    assert scores.news is not None and scores.news > 0.5
    assert scores.fundamentals is None
    assert scores.macro is None


def test_reddit_cap_uses_newest_twenty_records() -> None:
    """Ignore the oldest social record once the deterministic cap is reached."""
    records = [
        ContextRecord(
            kind="social",
            provider="reddit",
            external_id=f"post-{index}",
            title=f"$AAPL post {index}",
            source_at=NOW - timedelta(minutes=21 - index),
            received_at=NOW - timedelta(minutes=21 - index),
            symbols=("AAPL",),
            payload={"normalized_score": 0.0 if index == 0 else 1.0},
        )
        for index in range(21)
    ]

    scores = build_context_scores(records, symbol="AAPL", cutoff=NOW)

    assert scores.reddit == pytest.approx(1.0)
