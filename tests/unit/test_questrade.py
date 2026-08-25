from __future__ import annotations

import json
import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock

import pytest

from day_trading_engine.providers.questrade import (
    HttpResponse,
    QuestradeApiError,
    QuestradeAuthError,
    QuestradeClient,
    QuestradeNetworkError,
    TokenState,
    TokenStore,
)


class FakeTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = deque(responses)
        self.methods: list[str] = []
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.bodies: list[bytes | None] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        self.methods.append(method)
        self.urls.append(url)
        self.headers.append(headers)
        self.bodies.append(body)
        result = self.responses.popleft()
        if isinstance(result, Exception):
            raise result
        return result


def response(status: int, payload: dict[str, object], **headers: str) -> HttpResponse:
    return HttpResponse(status=status, headers=headers, body=json.dumps(payload).encode())


def auth_response(
    access_token: str = "access-1", refresh_token: str = "rotated-refresh"
) -> HttpResponse:
    return response(
        200,
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
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
    assert transport.methods == ["POST"]
    assert transport.urls == [QuestradeClient.AUTH_URL]
    assert b"refresh_token=initial-refresh" in (transport.bodies[0] or b"")
    assert transport.headers[0]["Content-Type"] == "application/x-www-form-urlencoded"
    assert not list(tmp_path.glob(".token.json.*.tmp"))
    assert store.load() is not None


def test_token_store_concurrent_saves_use_unique_temp_files(monkeypatch, tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "token.json")
    barrier = Barrier(2)
    replace_lock = Lock()
    original_replace = os.replace
    sources: list[Path] = []

    def interleaved_replace(src, dst) -> None:
        sources.append(Path(src))
        barrier.wait()
        with replace_lock:
            original_replace(src, dst)

    monkeypatch.setattr(os, "replace", interleaved_replace)

    def save(refresh_token: str) -> None:
        store.save(
            TokenState(
                access_token=f"access-{refresh_token}",
                refresh_token=refresh_token,
                api_server="https://api01.iq.questrade.com/",
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(save, ("one", "two")))

    assert len(set(sources)) == 2
    loaded = store.load()
    assert loaded is not None
    assert loaded.refresh_token in {"one", "two"}
    assert not list(tmp_path.glob(".token.json.*.tmp"))


def test_auth_post_failure_does_not_put_token_in_url(tmp_path: Path) -> None:
    transport = FakeTransport([response(403, {"error": "forbidden"})])
    client = QuestradeClient("refresh", TokenStore(tmp_path / "token.json"), transport=transport)

    with pytest.raises(QuestradeAuthError, match="HTTP 403"):
        client.authenticate()

    assert transport.methods == ["POST"]
    assert transport.urls == [QuestradeClient.AUTH_URL]
    assert "refresh" not in transport.urls[0]


def test_auth_falls_back_from_stale_cache_to_bootstrap_token(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "token.json")
    store.save(
        TokenState(
            access_token="expired-access",
            refresh_token="stale-cache",
            api_server="https://api01.iq.questrade.com/",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    transport = FakeTransport(
        [response(400, {"error": "invalid_grant"}), auth_response(refresh_token="fresh-rotated")]
    )
    client = QuestradeClient("fresh-bootstrap", store, transport=transport)

    state = client.authenticate()

    assert state.refresh_token == "fresh-rotated"
    assert b"refresh_token=stale-cache" in (transport.bodies[0] or b"")
    assert b"refresh_token=fresh-bootstrap" in (transport.bodies[1] or b"")


def test_auth_error_includes_safe_provider_detail(tmp_path: Path) -> None:
    transport = FakeTransport([response(403, {"error_description": "refresh token is invalid"})])
    client = QuestradeClient("refresh", TokenStore(tmp_path / "token.json"), transport=transport)

    with pytest.raises(QuestradeAuthError, match="refresh token is invalid"):
        client.authenticate()


def test_auth_rejects_malformed_success_payload(tmp_path: Path) -> None:
    transport = FakeTransport([response(200, {"access_token": "only-one-field"})])
    client = QuestradeClient("refresh", TokenStore(tmp_path / "token.json"), transport=transport)

    with pytest.raises(QuestradeAuthError, match="malformed"):
        client.authenticate()


def test_auth_retries_transient_network_error(tmp_path: Path) -> None:
    sleeps: list[float] = []
    transport = FakeTransport([QuestradeNetworkError("temporary"), auth_response()])
    client = QuestradeClient(
        "refresh",
        TokenStore(tmp_path / "token.json"),
        transport=transport,
        sleep=sleeps.append,
    )

    assert client.authenticate().access_token == "access-1"
    assert sleeps == [0.5]


def test_resolve_symbol_rejects_non_tradable_match(tmp_path: Path) -> None:
    symbol = {
        "symbol": "TEST",
        "symbolId": 7,
        "isQuotable": True,
        "isTradable": False,
    }
    transport = FakeTransport(
        [
            auth_response(),
            response(
                200,
                {"symbols": [symbol]},
                date="Mon, 24 Aug 2026 22:00:00 GMT",
            ),
        ]
    )
    client = QuestradeClient("refresh", TokenStore(tmp_path / "token.json"), transport=transport)

    with pytest.raises(QuestradeApiError, match="No tradable"):
        client.resolve_symbol("TEST")


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


def test_api_401_forces_token_refresh_after_network_retry(tmp_path: Path) -> None:
    transport = FakeTransport(
        [
            auth_response(),
            QuestradeNetworkError("temporary"),
            response(401, {"message": "expired"}),
            auth_response("access-2", "rotated-refresh-2"),
            response(200, {"markets": []}, date="Mon, 24 Aug 2026 22:00:00 GMT"),
        ]
    )
    client = QuestradeClient(
        "refresh", TokenStore(tmp_path / "token.json"), transport=transport, sleep=lambda _: None
    )

    assert client.get_markets() == ()
    assert transport.headers[-1]["Authorization"] == "Bearer access-2"


def test_api_call_retries_transient_network_error(tmp_path: Path) -> None:
    sleeps: list[float] = []
    transport = FakeTransport(
        [
            auth_response(),
            QuestradeNetworkError("temporary"),
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
    assert sleeps == [0.5]


def test_429_retries_with_retry_after_seconds(tmp_path: Path) -> None:
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


def test_retry_after_supports_http_date() -> None:
    retry_at = datetime.now(UTC) + timedelta(seconds=30)
    header = retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
    delay = QuestradeClient._retry_delay(response(429, {}, **{"retry-after": header}), 0)

    assert 28 <= delay <= 30


def test_non_retryable_api_error_is_explicit(tmp_path: Path) -> None:
    transport = FakeTransport([auth_response(), response(400, {"message": "bad request"})])
    client = QuestradeClient("refresh", TokenStore(tmp_path / "token.json"), transport=transport)

    with pytest.raises(QuestradeApiError, match="HTTP 400"):
        client.get_markets()
