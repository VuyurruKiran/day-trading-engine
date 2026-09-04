from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from day_trading_engine.providers import alpaca_history
from day_trading_engine.providers.alpaca_history import (
    AlpacaHistoryClient,
    AlpacaHistoryError,
)


def test_alpaca_history_maps_sip_bars_and_follows_pagination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        captured.append(request)
        assert timeout == 30
        second_page = "page_token=next-page" in request.full_url
        bar = {
            "t": "2026-04-01T13:31:00Z" if second_page else "2026-04-01T13:30:00Z",
            "o": 254.0,
            "h": 256.18,
            "l": 253.9,
            "c": 255.0 if second_page else 254.17,
            "v": 945846,
        }
        return io.BytesIO(
            json.dumps(
                {
                    "bars": [bar],
                    "next_page_token": None if second_page else "next-page",
                }
            ).encode()
        )

    monkeypatch.setattr(alpaca_history, "urlopen", fake_urlopen)
    client = AlpacaHistoryClient(["AAPL"], root=tmp_path)
    batch = client.get_candles(
        "AAPL",
        start=datetime(2026, 4, 1, 13, 30, tzinfo=UTC),
        end=datetime(2026, 4, 1, 20, 0, tzinfo=UTC),
    )

    assert len(batch.candles) == 2
    assert batch.candles[0].close == 254.17
    assert batch.candles[1].close == 255.0
    assert batch.candles[0].end == datetime(2026, 4, 1, 13, 31, tzinfo=UTC)
    assert "feed=sip" in captured[0].full_url
    assert "page_token=next-page" in captured[1].full_url
    assert captured[0].get_header("Apca-api-key-id") == "test-key"


def test_alpaca_history_retries_429_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    sleeps: list[float] = []
    attempts = {"count": 0}

    def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "3"},
                None,
            )
        return io.BytesIO(
            json.dumps(
                {
                    "bars": [
                        {
                            "t": "2026-04-01T13:30:00Z",
                            "o": 254.0,
                            "h": 256.18,
                            "l": 253.9,
                            "c": 254.17,
                            "v": 945846,
                        }
                    ]
                }
            ).encode()
        )

    monkeypatch.setattr(alpaca_history, "urlopen", fake_urlopen)
    monkeypatch.setattr(alpaca_history.time, "sleep", fake_sleep)
    client = AlpacaHistoryClient(["AAPL"], root=tmp_path)

    batch = client.get_candles(
        "AAPL",
        start=datetime(2026, 4, 1, 13, 30, tzinfo=UTC),
        end=datetime(2026, 4, 1, 20, 0, tzinfo=UTC),
    )

    assert len(batch.candles) == 1
    assert sleeps == [3.0]
    assert attempts["count"] == 2


def test_alpaca_history_retries_500_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    sleeps: list[float] = []
    attempts = {"count": 0}

    def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise HTTPError(request.full_url, 500, "Server Error", {}, None)
        return io.BytesIO(
            json.dumps(
                {
                    "bars": [
                        {
                            "t": "2026-04-01T13:30:00Z",
                            "o": 254.0,
                            "h": 256.18,
                            "l": 253.9,
                            "c": 254.17,
                            "v": 945846,
                        }
                    ]
                }
            ).encode()
        )

    monkeypatch.setattr(alpaca_history, "urlopen", fake_urlopen)
    monkeypatch.setattr(alpaca_history.time, "sleep", fake_sleep)
    client = AlpacaHistoryClient(["AAPL"], root=tmp_path)

    batch = client.get_candles(
        "AAPL",
        start=datetime(2026, 4, 1, 13, 30, tzinfo=UTC),
        end=datetime(2026, 4, 1, 20, 0, tzinfo=UTC),
    )

    assert len(batch.candles) == 1
    assert sleeps == [1]
    assert attempts["count"] == 2


def test_alpaca_history_retries_urlerror_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    sleeps: list[float] = []
    attempts = {"count": 0}

    def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise URLError("temporary failure")
        return io.BytesIO(
            json.dumps(
                {
                    "bars": [
                        {
                            "t": "2026-04-01T13:30:00Z",
                            "o": 254.0,
                            "h": 256.18,
                            "l": 253.9,
                            "c": 254.17,
                            "v": 945846,
                        }
                    ]
                }
            ).encode()
        )

    monkeypatch.setattr(alpaca_history, "urlopen", fake_urlopen)
    monkeypatch.setattr(alpaca_history.time, "sleep", fake_sleep)
    client = AlpacaHistoryClient(["AAPL"], root=tmp_path)

    batch = client.get_candles(
        "AAPL",
        start=datetime(2026, 4, 1, 13, 30, tzinfo=UTC),
        end=datetime(2026, 4, 1, 20, 0, tzinfo=UTC),
    )

    assert len(batch.candles) == 1
    assert sleeps == [1]
    assert attempts["count"] == 2


def test_alpaca_history_does_not_retry_permanent_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    sleeps: list[float] = []
    attempts = {"count": 0}

    def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        attempts["count"] += 1
        raise HTTPError(request.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(alpaca_history, "urlopen", fake_urlopen)
    monkeypatch.setattr(alpaca_history.time, "sleep", fake_sleep)
    client = AlpacaHistoryClient(["AAPL"], root=tmp_path)

    with pytest.raises(AlpacaHistoryError, match="HTTP 400"):
        client.get_candles(
            "AAPL",
            start=datetime(2026, 4, 1, 13, 30, tzinfo=UTC),
            end=datetime(2026, 4, 1, 20, 0, tzinfo=UTC),
        )

    assert sleeps == []
    assert attempts["count"] == 1


def test_alpaca_history_preserves_structured_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        body = io.BytesIO(
            json.dumps(
                {
                    "code": 42210000,
                    "message": "subscription does not permit querying recent SIP data",
                }
            ).encode()
        )
        raise HTTPError(request.full_url, 403, "Forbidden", {}, body)

    monkeypatch.setattr(alpaca_history, "urlopen", fake_urlopen)
    client = AlpacaHistoryClient(["AAPL"], root=tmp_path)

    with pytest.raises(
        AlpacaHistoryError,
        match="HTTP 403.*subscription does not permit querying recent SIP data",
    ):
        client.get_candles(
            "AAPL",
            start=datetime(2026, 4, 1, 13, 30, tzinfo=UTC),
            end=datetime(2026, 4, 1, 20, 0, tzinfo=UTC),
        )


def test_alpaca_history_paces_concurrent_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    sleeps: list[float] = []
    ticks = iter((10.0, 10.1))
    monkeypatch.setattr(alpaca_history.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(alpaca_history.time, "sleep", sleeps.append)
    client = AlpacaHistoryClient(["AAPL"], root=tmp_path)

    client._pace_request()
    client._pace_request()

    assert sleeps == pytest.approx([0.21])


def test_alpaca_history_raises_after_max_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    sleeps: list[float] = []
    attempts = {"count": 0}

    def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        attempts["count"] += 1
        raise HTTPError(request.full_url, 500, "Server Error", {}, None)

    monkeypatch.setattr(alpaca_history, "urlopen", fake_urlopen)
    monkeypatch.setattr(alpaca_history.time, "sleep", fake_sleep)
    client = AlpacaHistoryClient(["AAPL"], root=tmp_path)

    with pytest.raises(alpaca_history.AlpacaHistoryError, match="HTTP 500"):
        client.get_candles(
            "AAPL",
            start=datetime(2026, 4, 1, 13, 30, tzinfo=UTC),
            end=datetime(2026, 4, 1, 20, 0, tzinfo=UTC),
        )

    assert sleeps == [1, 2, 4]
    assert attempts["count"] == 4


def test_alpaca_history_rejects_duplicate_symbols(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate symbol"):
        AlpacaHistoryClient(["AAPL", "AAPL"], root=tmp_path)


def test_alpaca_confirms_missing_bar_from_odd_lot_trades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        assert "/trades?" in request.full_url
        return io.BytesIO(
            json.dumps(
                {
                    "trades": [
                        {
                            "t": "2026-04-01T13:31:10Z",
                            "s": 25,
                            "c": ["@", "I"],
                            "z": "C",
                        }
                    ],
                    "next_page_token": None,
                }
            ).encode()
        )

    monkeypatch.setattr(alpaca_history, "urlopen", fake_urlopen)
    client = AlpacaHistoryClient(["AAPL"], root=tmp_path)

    assert client.missing_minutes_have_no_bar_eligible_trades(
        "AAPL", ("2026-04-01T13:31:00+00:00",)
    )


def test_alpaca_treats_null_trades_as_no_trades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    monkeypatch.setattr(
        alpaca_history,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(
            json.dumps({"trades": None, "next_page_token": None}).encode()
        ),
    )
    client = AlpacaHistoryClient(["AAPL"], root=tmp_path)

    assert client.missing_minutes_have_no_bar_eligible_trades(
        "AAPL", ("2026-04-01T13:31:00+00:00",)
    )


@pytest.mark.parametrize(
    ("trade", "can_create_bar"),
    [
        ({"s": 100, "c": ["@"], "z": "C"}, True),
        ({"s": 10, "c": ["@", "I"], "z": "C"}, False),
        ({"s": 100, "c": ["B"], "z": "A"}, False),
        ({"s": 100, "c": ["B"], "z": "C"}, True),
        ({"s": 100, "c": ["?"], "z": "C"}, True),
    ],
)
def test_alpaca_minute_bar_trade_condition_rules(
    trade: dict[str, object], can_create_bar: bool
) -> None:
    assert alpaca_history._trade_can_create_minute_bar(trade) is can_create_bar
