from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from day_trading_engine.context.collector import (
    _gdelt_security_query,
    _merge_news_associations,
)
from day_trading_engine.context.models import ContextRecord
from day_trading_engine.context.store import ContextStore
from day_trading_engine.core.config import load_config
from day_trading_engine.engine.runner import run_decision
from day_trading_engine.engine.strategy import CandidateSnapshot, _technical_score
from day_trading_engine.features.context import build_context_scores
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.providers.questrade import Quote, ResponseMeta
from day_trading_engine.ui.state import ReportStore

NOW = datetime(2026, 8, 25, 13, 37, tzinfo=UTC)


def test_duplicate_context_selection_is_order_independent() -> None:
    older = ContextRecord(
        kind="news",
        provider="test",
        external_id="old",
        title="AAPL catalyst",
        source_at=NOW - timedelta(minutes=5),
        received_at=NOW - timedelta(minutes=4),
        symbols=("AAPL",),
        payload={"normalized_score": 0.1},
    )
    newer = ContextRecord(
        kind="news",
        provider="test",
        external_id="new",
        title="AAPL catalyst",
        source_at=NOW - timedelta(minutes=3),
        received_at=NOW - timedelta(minutes=2),
        symbols=("AAPL",),
        payload={"normalized_score": 0.9},
    )
    forward = build_context_scores([older, newer], symbol="AAPL", cutoff=NOW)
    reverse = build_context_scores([newer, older], symbol="AAPL", cutoff=NOW)
    assert forward.news == reverse.news


def test_duplicate_gdelt_news_keeps_associations_and_provider_order() -> None:
    first = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="url-1",
        title="Shared catalyst",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
    )
    social = ContextRecord(
        kind="social",
        provider="reddit",
        external_id="post-1",
        title="$AAPL discussion",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
    )
    second = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="url-2",
        title="Shared catalyst",
        source_at=NOW,
        received_at=NOW,
        symbols=("MSFT",),
    )
    merged = _merge_news_associations((first, social, second))
    assert [record.kind for record in merged] == ["news", "social"]
    assert set(merged[0].symbols) == {"AAPL", "MSFT"}


def test_gdelt_query_uses_security_notation_for_ordinary_word_tickers() -> None:
    query = _gdelt_security_query("cat")
    assert query == '(near10:"CAT NYSE" OR near10:"CAT NASDAQ" OR "$CAT")'
    assert query != "CAT"


def test_context_store_unions_news_symbols_without_time_travel(tmp_path: Path) -> None:
    earlier = NOW - timedelta(minutes=10)
    first = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="url-1",
        title="Shared catalyst",
        source_at=earlier,
        received_at=earlier,
        symbols=("AAPL",),
    )
    later = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="url-2",
        title="Shared catalyst",
        source_at=earlier,
        received_at=NOW,
        symbols=("MSFT",),
    )

    with ContextStore(tmp_path / "context.db") as store:
        assert store.add_many((first,)) == 1
        assert store.add_many((later,)) == 0
        before_second_collection = store.as_of(NOW - timedelta(minutes=5))
        after_second_collection = store.as_of(NOW)

    assert before_second_collection[0].symbols == ("AAPL",)
    assert set(after_second_collection[0].symbols) == {"AAPL", "MSFT"}


def test_context_store_reverse_news_import_keeps_earliest_content(tmp_path: Path) -> None:
    earlier = NOW - timedelta(minutes=10)
    later = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="url-late",
        title="Replay catalyst",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
        payload={"normalized_score": 0.9},
    )
    first_known = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="url-early",
        title="Replay catalyst",
        source_at=earlier,
        received_at=earlier,
        symbols=("AAPL",),
        payload={"normalized_score": 0.1},
    )

    with ContextStore(tmp_path / "context.db") as store:
        store.add_many((later, first_known))
        rows = store.as_of(NOW - timedelta(minutes=5))

    assert len(rows) == 1
    assert rows[0].external_id == "url-early"
    assert rows[0].received_at == earlier
    assert rows[0].source_at == earlier
    assert rows[0].symbols == ("AAPL",)
    assert rows[0].payload["normalized_score"] == 0.1


def test_context_store_preserves_global_news_scope_across_merges(tmp_path: Path) -> None:
    earlier = NOW - timedelta(minutes=10)
    scoped = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="scoped",
        title="Global catalyst",
        source_at=earlier,
        received_at=earlier,
        symbols=("AAPL",),
    )
    global_record = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="global",
        title="Global catalyst",
        source_at=earlier,
        received_at=NOW,
        symbols=(),
    )

    with ContextStore(tmp_path / "scoped-first.db") as store:
        store.add_many((scoped, global_record))
        before_global = store.as_of(NOW - timedelta(minutes=5))
        after_global = store.as_of(NOW)

    assert before_global[0].symbols == ("AAPL",)
    assert after_global[0].symbols == ()

    with ContextStore(tmp_path / "global-first.db") as store:
        store.add_many((global_record, scoped))
        rows = store.as_of(NOW)

    assert rows[0].symbols == ()


def test_normalized_filing_and_macro_scores_decay_from_source_time() -> None:
    stale = NOW - timedelta(hours=6)
    records = [
        ContextRecord(
            kind=kind,
            provider="test",
            external_id=kind,
            title=f"AAPL {kind}",
            source_at=stale,
            received_at=stale,
            symbols=("AAPL",),
            payload={"normalized_score": 1.0},
        )
        for kind in ("filing", "macro")
    ]

    scores = build_context_scores(records, symbol="AAPL", cutoff=NOW)

    assert scores.fundamentals == 0.75
    assert scores.macro == 0.75


def test_highly_upvoted_negative_reddit_title_stays_negative() -> None:
    record = ContextRecord(
        kind="social",
        provider="reddit",
        external_id="post-1",
        title="$AAPL fraud probe and losses",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
        payload={"score": 10_000, "num_comments": 10_000, "upvote_ratio": 0.99},
    )
    scores = build_context_scores([record], symbol="AAPL", cutoff=NOW)
    assert scores.reddit is not None and scores.reddit < 0.5


def test_production_technical_score_is_normalized() -> None:
    row = CandidateSnapshot(
        symbol="AAPL",
        price=60.0,
        bid=59.9,
        ask=60.1,
        volume=1_000_000,
        rvol=20.0,
        volatility=0.01,
        vwap=50.0,
        opening_range_high=55.0,
        market_relative_strength=0.2,
        sector_relative_strength=0.2,
    )
    assert 0.0 <= _technical_score(row) <= 1.0


def _seed_market(store: MarketDataStore) -> None:
    start = datetime(2026, 8, 25, 13, 30, tzinfo=UTC)
    for symbol_index in range(30):
        symbol = f"T{symbol_index:02d}"
        base = 10.0 + symbol_index / 10
        for minute in range(7):
            at = start + timedelta(minutes=minute)
            price = base + minute * 0.05
            store.store_quote(
                Quote(
                    symbol=symbol,
                    symbolId=10_000 + symbol_index,
                    bidPrice=price - 0.01,
                    bidSize=100,
                    askPrice=price + 0.01,
                    askSize=100,
                    lastTradePrice=price,
                    volume=100_000 + minute * 10_000,
                    openPrice=base,
                    highPrice=price,
                    lowPrice=base,
                    delay=0,
                    isHalted=False,
                ),
                ResponseMeta(
                    source_at=at,
                    received_at=at,
                    source_time_origin="http_date",
                    latency_ms=1,
                    rate_limit_remaining=100,
                    rate_limit_reset=None,
                ),
            )


def test_decision_cutoff_includes_context_collected_at_creation_time(tmp_path: Path) -> None:
    config = load_config(Path("configs/v1.yaml"))
    config = config.model_copy(
        update={
            "project": config.project.model_copy(update={"decision_time": "07:30"}),
            "market_data": config.market_data.model_copy(
                update={"watchlist": tuple(f"T{index:02d}" for index in range(30))}
            ),
        }
    )
    market_store = MarketDataStore(tmp_path / "trading.db")
    report_store = ReportStore(tmp_path / "decision_state.db")
    _seed_market(market_store)
    with ContextStore(tmp_path / "context.db") as context_store:
        context_store.add_many(
            [
                ContextRecord(
                    kind="news",
                    provider="test",
                    external_id="fresh",
                    title="T00 catalyst",
                    source_at=NOW - timedelta(minutes=1),
                    received_at=NOW,
                    symbols=("T00",),
                    payload={"normalized_score": 1.0},
                )
            ]
        )
    report = run_decision(
        config=config,
        market_store=market_store,
        report_store=report_store,
        created_at=NOW,
    )
    t00 = next(row for row in report.payload["cohort"] if row["symbol"] == "T00")
    assert t00["context"]["news_score"] < 1.0
    assert t00["context"]["news_score"] > 0.5
