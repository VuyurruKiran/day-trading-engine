from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.context.store import ContextStore
from day_trading_engine.core.config import load_config
from day_trading_engine.engine.discovery import load_scan_universe, select_research_symbols
from day_trading_engine.engine.live import (
    _history_start,
    _previous_trading_session,
    _refresh_context,
    _start_background_backfill,
)
from day_trading_engine.market_data.store import StoredQuote

ROOT = Path(__file__).resolve().parents[2]


def _quote(
    symbol: str,
    index: int,
    *,
    eligible: bool = True,
    volume: int | None = None,
) -> StoredQuote:
    return StoredQuote(
        symbol=symbol,
        symbol_id=index,
        bid_price=10.0,
        bid_size=100,
        ask_price=10.01,
        ask_size=100,
        last_trade_price=10.0,
        volume=1_000_000 - index if volume is None else volume,
        open_price=10.0,
        high_price=10.1,
        low_price=9.9,
        delay_seconds=0,
        is_halted=False,
        source_at="2026-08-27T14:00:00+00:00",
        received_at="2026-08-27T14:00:00+00:00",
        source_time_origin="test",
        latency_ms=1,
        rate_limit_remaining=100,
        rate_limit_reset=None,
        is_trade_eligible=eligible,
        invalid_reason=None if eligible else "invalid",
    )


def _write_universe(root: Path, symbols: list[str]) -> None:
    """Write a temporary scan universe using the production config path."""
    configs = root / "configs"
    configs.mkdir(parents=True)
    (configs / "us_scan_universe.txt").write_text("\n".join(symbols), encoding="utf-8")


def test_live_scan_reduces_200_validated_symbols_to_30() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe = load_scan_universe(ROOT, config)
    quotes = tuple(
        _quote(symbol, index, eligible=index != 0)
        for index, symbol in enumerate(universe)
    )

    selected = select_research_symbols(quotes, config=config, session_key="2026-08-27")

    assert len(universe) == 200
    assert len(selected) == len(set(selected)) == 30
    assert universe[0] not in selected


def test_live_scan_returns_shortfall_when_only_29_quotes_are_usable() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe = load_scan_universe(ROOT, config)
    quotes = tuple(
        _quote(symbol, index, eligible=index < 29)
        for index, symbol in enumerate(universe)
    )

    selected = select_research_symbols(quotes, config=config, session_key="2026-08-27")

    assert len(selected) == 29


def test_live_scan_rejects_missing_volume_before_selection() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe = load_scan_universe(ROOT, config)
    quotes = tuple(
        _quote(symbol, index, volume=0 if index else None)
        for index, symbol in enumerate(universe[:31])
    )
    first = quotes[0]
    quotes = (
        StoredQuote(**{**first.__dict__, "volume": None}),
        *quotes[1:],
    )

    selected = select_research_symbols(quotes, config=config, session_key="2026-08-27")

    assert first.symbol not in selected
    assert len(selected) == 30


def test_scan_universe_rejects_wrong_size(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    _write_universe(tmp_path, list(config.market_data.watchlist))

    with pytest.raises(ValueError, match="exactly 200"):
        load_scan_universe(tmp_path, config)


def test_scan_universe_rejects_duplicates(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe = list(load_scan_universe(ROOT, config))
    universe[-1] = universe[0]
    _write_universe(tmp_path, universe)

    with pytest.raises(ValueError, match="duplicate"):
        load_scan_universe(tmp_path, config)


def test_scan_universe_requires_configured_watchlist(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    universe = list(load_scan_universe(ROOT, config))
    missing = config.market_data.watchlist[0]
    universe[universe.index(missing)] = "ZZZZ"
    _write_universe(tmp_path, universe)

    with pytest.raises(ValueError, match="watchlist"):
        load_scan_universe(tmp_path, config)


def test_history_start_handles_month_end() -> None:
    assert _history_start(date(2024, 2, 29), 12) == date(2023, 2, 28)


def test_previous_trading_session_skips_weekend() -> None:
    assert _previous_trading_session(date(2026, 8, 24)) == date(2026, 8, 21)


def test_previous_trading_session_skips_market_holiday() -> None:
    assert _previous_trading_session(date(2026, 7, 6)) == date(2026, 7, 2)


def test_background_backfill_launches_existing_maintenance_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_popen(command, *, cwd):
        captured["command"] = command
        captured["cwd"] = cwd
        return sentinel

    monkeypatch.setattr("day_trading_engine.engine.live.subprocess.Popen", fake_popen)

    result = _start_background_backfill(
        tmp_path,
        ("AAPL", "MSFT"),
        end=date(2026, 8, 27),
        as_of=date(2026, 8, 28),
        months=24,
    )

    assert result is sentinel
    assert captured["cwd"] == tmp_path
    assert captured["command"][2:] == [
        "day_trading_engine.ops.maintenance",
        "--root",
        str(tmp_path),
        "backfill",
        "--start",
        "2024-08-27",
        "--end",
        "2026-08-27",
        "--universe-as-of",
        "2026-08-28",
        "AAPL",
        "MSFT",
    ]


def test_refresh_context_persists_runtime_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 28, 14, 5, tzinfo=UTC)
    record = ContextRecord(
        kind="news",
        provider="gdelt",
        external_id="n1",
        title="AAPL beats estimates",
        source_at=now,
        received_at=now,
        symbols=("AAPL",),
        payload={"direction": "positive"},
    )
    result = SimpleNamespace(records=(record,), errors=("reddit: unavailable",))
    monkeypatch.setattr(
        "day_trading_engine.engine.live.collect_public_context",
        lambda symbols: result,
    )
    (tmp_path / "data").mkdir()

    added, completed_at = _refresh_context(
        tmp_path,
        ("AAPL",),
        software_version="0.1.0",
    )

    assert added == 1
    with ContextStore(tmp_path / "data" / "context.db") as store:
        assert [row.external_id for row in store.as_of(completed_at)] == ["n1"]
        run = store._connection.execute(
            "SELECT record_count, errors, versions FROM context_collection_runs"
        ).fetchone()
    assert run is not None
    assert run[0] == 1
    assert "reddit: unavailable" in run[1]
    assert '"software": "0.1.0"' in run[2]
