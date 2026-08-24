from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from day_trading_engine.market_data.collector import QuestradeCollector, _load_refresh_token
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.providers.questrade import (
    QuestradeApiError,
    Quote,
    QuoteBatch,
    ResponseMeta,
    SymbolMatch,
)


class FakeClient:
    def __init__(self) -> None:
        self.requested_ids: list[int] = []

    def resolve_symbol(self, symbol: str) -> SymbolMatch:
        if symbol == "BAD":
            raise QuestradeApiError("not found")
        return SymbolMatch(symbol=symbol, symbolId={"AAPL": 1, "AMD": 2}[symbol])

    def get_quotes(self, symbol_ids: list[int], batch_size: int = 50) -> tuple[QuoteBatch, ...]:
        self.requested_ids = symbol_ids
        now = datetime.now(UTC)
        meta = ResponseMeta(now, now, "http_date", 0, 100, 60)
        quotes = tuple(
            Quote(
                symbol="AAPL" if symbol_id == 1 else "AMD",
                symbolId=symbol_id,
                bidPrice=10.0,
                askPrice=10.1,
                lastTradePrice=10.05,
                delay=0,
                isHalted=False,
            )
            for symbol_id in symbol_ids
            if symbol_id != 2
        )
        return (QuoteBatch(quotes=quotes, meta=meta),)


def test_collector_deduplicates_isolates_resolution_and_detects_missing_quote(tmp_path: Path) -> None:
    client = FakeClient()
    collector = QuestradeCollector(client, MarketDataStore(tmp_path / "trading.db"))  # type: ignore[arg-type]

    result = collector.collect([" aapl ", "AAPL", "BAD", "AMD"])

    assert client.requested_ids == [1, 2]
    assert [item.symbol for item in result.stored] == ["AAPL"]
    assert result.failed_symbols == ("BAD", "AMD")


def test_refresh_token_prefers_environment(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("QUESTRADE_REFRESH_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.setenv("QUESTRADE_REFRESH_TOKEN", "env-token")

    assert _load_refresh_token(tmp_path) == "env-token"


def test_refresh_token_falls_back_to_dotenv(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("QUESTRADE_REFRESH_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "# comment\nOTHER=x\nQUESTRADE_REFRESH_TOKEN='file-token'\n", encoding="utf-8"
    )

    assert _load_refresh_token(tmp_path) == "file-token"


def test_refresh_token_missing_returns_empty(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("QUESTRADE_REFRESH_TOKEN", raising=False)

    assert _load_refresh_token(tmp_path) == ""
