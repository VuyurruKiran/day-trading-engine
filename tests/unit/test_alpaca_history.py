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
