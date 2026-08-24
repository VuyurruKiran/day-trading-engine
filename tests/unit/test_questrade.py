from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import pytest

from day_trading_engine.providers.questrade import (
    HttpResponse,
    QuestradeApiError,
    QuestradeClient,
    TokenStore,
)


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = deque(responses)
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def request(self, method: str, url: str, headers: dict[str, str]) -> HttpResponse:
        assert method == "GET"
        self.urls.append(url)
        self.headers.append(headers)
        return self.responses.popleft()


def response(status: int, payload: dict[str, object], **headers: str) -> HttpResponse:
    return HttpResponse(status=status, headers=headers, body=json.dumps(payload).encode())


def auth_response() -> HttpResponse:
    return response(
        200,
        {
            "access_token": "access-1",
            "refresh_token": "rotated-refresh",
            "api_server": "https://api01.iq.questrade.com/",
            "expires_in": 1800,
        },
    )


def test_auth_rotates_and_persists_refresh_token(tmp_path: Path) -> None:
    transport = FakeTransport([auth_response()])
    store = TokenStore(tmp_path / "token.json")
    client = QuestradeClient("initial-refresh", store, transport=transport)

    state = client.authenticate()

    assert state.refresh_token == "rotated-refresh"
    assert "initial-refresh" in transport.urls[0]
    assert store.load() is not None
    assert store.load().refresh_token == "rotated-refresh"  # type: ignore[union-attr]


def test_quote_call_tracks_rate_limit_and_source_time(tmp_path: Path) -> None:
    date_header = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    transport = FakeTransport(
        [
            auth_response(),
            response(
                200,
                {
                    "quotes": [
                        {
                            "symbol": "AAPL",
                            "symbolId": 8049,
                            "bidPrice": 220.1,
                            "askPrice": 220.2,
                            "lastTradePrice": 220.15,
                            "volume": 1000000,
                            "delay": 0,
                            "isHalted": False,
                        }
                    ]
                },
                date=date_header,
                **{"x-ratelimit-remaining": "14999", "x-ratelimit-reset": "60"},
            ),
        ]
    )
    client = QuestradeClient("refresh", TokenStore(tmp_path / "token.json"), transport=transport)

    batches = client.get_quotes([8049])

    assert batches[0].quotes[0].symbol == "AAPL"
    assert batches[0].meta.source_time_origin == "http_date"
    assert batches[0].meta.rate_limit_remaining == 14999
    assert transport.headers[-1]["Authorization"] == "Bearer access-1"


def test_429_retries_with_retry_after(tmp_path: Path) -> None:
    sleeps: list[float] = []
    transport = FakeTransport(
        [
            auth_response(),
            response(429, {"message": "rate limited"}, **{"retry-after": "2"}),
            response(200, {"markets": []}, date="Mon, 24 Aug 2026 22:00:00 GMT"),
        ]
    )
    client = QuestradeClient(
        "refresh",
        TokenStore(tmp_path / "token.json"),
        transport=transport,
        sleep=sleeps.append,
    )

    assert client.get_markets() == ()
    assert sleeps == [2.0]


def test_non_retryable_api_error_is_explicit(tmp_path: Path) -> None:
    transport = FakeTransport([auth_response(), response(400, {"message": "bad request"})])
    client = QuestradeClient("refresh", TokenStore(tmp_path / "token.json"), transport=transport)

    with pytest.raises(QuestradeApiError, match="HTTP 400"):
        client.get_markets()
