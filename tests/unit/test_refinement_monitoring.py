from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.providers.questrade import Quote, ResponseMeta
from day_trading_engine.research.dataset import ResearchDatasetStore


def _row(index: int, *, finalist: bool = False, primary: bool = False) -> dict[str, object]:
    symbol = "AAPL" if index == 0 else f"S{index:02d}"
    plan = None
    if finalist:
        plan = {
            "symbol": symbol,
            "entry": 100.0,
            "stop": 95.0,
            "target": 105.0,
            "quantity": 1,
        }
    return {
        "symbol": symbol,
        "eligible": True,
        "rank_score": 1.0 - index / 100.0,
        "technical_score": 0.8,
        "features": {"price": 100.0, "market_score": 0.7},
        "context": {
            "market_score": 0.7,
            "news_score": 0.6,
            "social_score": 0.55,
            "fundamental_score": 0.65,
            "evidence_counts": {"news": 2},
        },
        "reasons": ["eligible"],
        "cohort_rank": index + 1,
        "cohort_reason": "research cohort",
        "finalist": finalist,
        "primary": primary,
        "plan": plan,
    }


def _quote(price: float, at: datetime) -> tuple[Quote, ResponseMeta]:
    quote = Quote(
        symbol="AAPL",
        symbolId=1,
        bidPrice=price - 0.05,
        askPrice=price + 0.05,
        lastTradePrice=price,
        volume=10_000,
        delay=0,
        isHalted=False,
    )
    return quote, ResponseMeta(at, at, "http_date", 0, 100, 60)


def test_selection_explanations_persist_finalists_and_three_controls(tmp_path: Path) -> None:
    store = ResearchDatasetStore(tmp_path / "research.db")
    rows = [
        _row(index, finalist=index < 5, primary=index == 0)
        for index in range(10)
    ]

    store.save_selection_explanations("2026-08-30-test", rows)

    with sqlite3.connect(tmp_path / "trading.db") as db:
        saved = db.execute(
            "SELECT symbol, role, final_rank, explanation_json "
            "FROM research_selections ORDER BY final_rank"
        ).fetchall()

    assert len(saved) == 8
    assert [row[1] for row in saved[:5]] == ["PRIMARY", *(["FINALIST"] * 4)]
    assert [row[1] for row in saved[5:]] == ["CONTROL"] * 3
    explanation = json.loads(saved[0][3])
    assert explanation["fundamental_score"] == pytest.approx(0.65)
    assert explanation["effective_weights"] == {
        "technical": 0.5,
        "market": 0.2,
        "news": 0.2,
        "reddit": 0.05,
        "fundamentals": 0.05,
    }


def test_selection_controls_skip_ineligible_near_misses(tmp_path: Path) -> None:
    rows = [_row(index, finalist=index < 2, primary=index == 0) for index in range(7)]
    rows[2]["eligible"] = False

    ResearchDatasetStore(tmp_path / "research.db").save_selection_explanations(
        "2026-08-30-test", rows
    )

    with sqlite3.connect(tmp_path / "trading.db") as db:
        controls = db.execute(
            "SELECT symbol FROM research_selections WHERE role = 'CONTROL' ORDER BY final_rank"
        ).fetchall()

    assert controls == [("S03",), ("S04",), ("S05",)]


def test_market_store_records_one_refinement_snapshot_per_five_minutes(tmp_path: Path) -> None:
    ResearchDatasetStore(tmp_path / "research.db").save_selection_explanations(
        "2026-08-30-test",
        [_row(index, finalist=index < 5, primary=index == 0) for index in range(8)],
    )
    market = MarketDataStore(tmp_path / "trading.db")

    for price, minute in ((101.0, 0), (104.0, 3), (106.0, 5)):
        at = datetime(2026, 8, 30, 14, minute, tzinfo=UTC)
        quote, meta = _quote(price, at)
        market.store_quote(quote, meta)

    with sqlite3.connect(tmp_path / "trading.db") as db:
        rows = db.execute(
            """
            SELECT bucket_at, return_pct, mfe_pct, mae_pct, target_hit, stop_hit
            FROM research_monitoring
            WHERE snapshot_id = ? AND symbol = 'AAPL'
            ORDER BY observed_at
            """,
            ("2026-08-30-test",),
        ).fetchall()

    assert len(rows) == 2
    assert rows[0][1:4] == pytest.approx((1.0, 1.0, 1.0))
    assert rows[1][1:4] == pytest.approx((6.0, 6.0, 1.0))
    assert rows[0][4:] == (0, 0)
    assert rows[1][4:] == (1, 0)
