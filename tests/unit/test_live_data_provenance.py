from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import day_trading_engine.ui.server as ui_server
from day_trading_engine.market_data.collector import QuestradeCollector
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.providers.questrade import Quote, QuoteBatch, ResponseMeta, SymbolMatch
from day_trading_engine.ui.state import ReportStore, SavedReport


def _meta(at: datetime) -> ResponseMeta:
    return ResponseMeta(at, at, "http_date", 0, 100, 60)


def _quote(symbol: str, symbol_id: int, price: float) -> Quote:
    return Quote(
        symbol=symbol,
        symbolId=symbol_id,
        bidPrice=price - 0.01,
        askPrice=price + 0.01,
        lastTradePrice=price,
        delay=0,
        isHalted=False,
    )


def test_live_store_rejects_non_questrade_provenance_but_allows_large_moves(
    tmp_path: Path,
) -> None:
    store = MarketDataStore(tmp_path / "trading.db")
    start = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
    store.store_quote(_quote("AAPL", 1, 10.0), _meta(start))
    store.store_quote(_quote("AAPL", 1, 320.0), _meta(start + timedelta(minutes=1)))

    assert store.latest("AAPL").last_trade_price == 320.0
    assert len(store.session("AAPL", "2026-08-28")) == 2

    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE market_quotes SET provider = 'synthetic'")
        db.commit()

    assert store.latest("AAPL") is None
    assert store.latest_all() == ()
    assert store.session("AAPL", "2026-08-28") == ()


def test_live_store_migrates_legacy_uniqueness_without_blocking_live_quote(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trading.db"
    received = "2026-08-28T14:00:00+00:00"
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TABLE market_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                symbol_id INTEGER NOT NULL,
                bid_price REAL,
                bid_size INTEGER,
                ask_price REAL,
                ask_size INTEGER,
                last_trade_price REAL,
                volume INTEGER,
                open_price REAL,
                high_price REAL,
                low_price REAL,
                delay_seconds INTEGER NOT NULL,
                is_halted INTEGER NOT NULL,
                source_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                source_time_origin TEXT NOT NULL,
                latency_ms INTEGER NOT NULL,
                rate_limit_remaining INTEGER,
                rate_limit_reset INTEGER,
                is_trade_eligible INTEGER NOT NULL,
                invalid_reason TEXT,
                UNIQUE(symbol_id, received_at)
            )
            """
        )
        db.execute(
            """
            INSERT INTO market_quotes(
                symbol, symbol_id, bid_price, ask_price, last_trade_price,
                delay_seconds, is_halted, source_at, received_at,
                source_time_origin, latency_ms, is_trade_eligible
            ) VALUES ('AAPL', 1, 9.99, 10.01, 10.0, 0, 0, ?, ?, 'http_date', 0, 1)
            """,
            (received, received),
        )

    store = MarketDataStore(path)
    at = datetime.fromisoformat(received)
    store.store_quote(_quote("AAPL", 1, 320.0), _meta(at))

    assert store.latest("AAPL").last_trade_price == 320.0
    with sqlite3.connect(path) as db:
        providers = db.execute(
            "SELECT provider FROM market_quotes WHERE symbol_id = 1 AND received_at = ? ORDER BY provider",
            (received,),
        ).fetchall()
    assert providers == [("questrade",), ("unknown",)]


def test_collector_does_not_store_symbol_id_mismatch(tmp_path: Path) -> None:
    class Client:
        def resolve_symbol(self, symbol: str) -> SymbolMatch:
            return SymbolMatch(symbol=symbol, symbolId=1)

        def get_quotes(self, symbol_ids: list[int], batch_size: int = 50) -> tuple[QuoteBatch, ...]:
            at = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
            return (QuoteBatch(quotes=(_quote("AMD", 1, 100.0),), meta=_meta(at)),)

    store = MarketDataStore(tmp_path / "trading.db")
    result = QuestradeCollector(Client(), store).collect(["AAPL"])

    assert result.stored == ()
    assert result.failed_symbols == ("AAPL",)
    assert store.latest_all() == ()


def test_stale_primary_is_not_actionable_in_ui_state(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    store = ReportStore(data / "decision_state.db")
    store.save_once(
        SavedReport(
            snapshot_id="2000-01-03-old",
            created_at=datetime(2000, 1, 3, 16, 0, tzinfo=UTC),
            primary_symbol="AAPL",
            payload={
                "session": "2000-01-03",
                "decision_state": "PRIMARY",
                "primary": {"symbol": "AAPL", "entry": 10.0},
            },
        )
    )
    health = SimpleNamespace(to_dict=lambda: {"ok": True})
    config = SimpleNamespace(project=SimpleNamespace(timezone="America/Edmonton"))
    monkeypatch.setattr(ui_server, "run_health_check", lambda _: (health, config))
    monkeypatch.setattr(ui_server, "ensure_runtime_dirs", lambda _: (data, tmp_path / "logs"))

    latest = ui_server._state_payload(tmp_path)["latest"]

    assert latest["stale"] is True
    assert latest["primary_symbol"] is None
    assert latest["payload"]["decision_state"] == "STALE"
    assert latest["payload"]["primary"]["symbol"] == "AAPL"


def test_stale_primary_entry_is_rejected_at_post_boundary(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    ReportStore(data / "decision_state.db").save_once(
        SavedReport(
            snapshot_id="2000-01-03-old",
            created_at=datetime(2000, 1, 3, 16, 0, tzinfo=UTC),
            primary_symbol="AAPL",
            payload={"session": "2000-01-03", "decision_state": "PRIMARY"},
        )
    )
    config = SimpleNamespace(project=SimpleNamespace(timezone="America/Edmonton"))
    monkeypatch.setattr(ui_server, "load_config", lambda _: config)
    monkeypatch.setattr(ui_server, "ensure_runtime_dirs", lambda _: (data, tmp_path / "logs"))

    with pytest.raises(ValueError, match="current-session PRIMARY"):
        ui_server._apply_trade(
            tmp_path,
            "2000-01-03-old",
            "entry",
            {"at": "2000-01-03T10:01", "price": 10.0, "quantity": 1},
        )
