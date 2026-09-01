from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request

import pytest

from day_trading_engine.providers import alpaca_catalog
from day_trading_engine.providers.alpaca_catalog import (
    AlpacaCatalogClient,
    AlpacaCatalogError,
)


def _credentials(root: Path) -> None:
    (root / ".env").write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )


def test_alpaca_catalog_reads_assets_and_paged_daily_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    _credentials(tmp_path)
    requests: list[Request] = []

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        requests.append(request)
        assert timeout == 30
        if "/v2/assets" in request.full_url:
            return io.BytesIO(
                json.dumps(
                    [
                        {
                            "id": "asset-aapl",
                            "symbol": "AAPL",
                            "name": "Apple Inc",
                            "exchange": "NASDAQ",
                            "status": "active",
                            "tradable": True,
                            "attributes": ["has_options"],
                        }
                    ]
                ).encode()
            )
        second_page = "page_token=next" in request.full_url
        timestamp = "2026-08-28T04:00:00Z" if second_page else "2026-08-27T04:00:00Z"
        return io.BytesIO(
            json.dumps(
                {
                    "bars": {
                        "AAPL": [
                            {"t": timestamp, "h": 101, "l": 99, "c": 100, "v": 1_000_000}
                        ]
                    },
                    "next_page_token": None if second_page else "next",
                }
            ).encode()
        )

    monkeypatch.setattr(alpaca_catalog, "urlopen", fake_urlopen)
    client = AlpacaCatalogClient(root=tmp_path)

    assets = client.list_active_us_assets()
    bars = client.get_daily_bars(
        ["AAPL"], start=date(2026, 8, 27), end=date(2026, 8, 28)
    )

    assert assets[0].asset_id == "asset-aapl"
    assert assets[0].attributes == ("has_options",)
    assert [bar.session for bar in bars["AAPL"]] == [
        date(2026, 8, 27),
        date(2026, 8, 28),
    ]
    assert "status=active" in requests[0].full_url
    assert "timeframe=1Day" in requests[1].full_url
    assert "page_token=next" in requests[2].full_url
    assert requests[0].get_header("Apca-api-key-id") == "test-key"


def test_alpaca_catalog_rejects_repeated_page_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    _credentials(tmp_path)
    client = AlpacaCatalogClient(root=tmp_path)
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda _: {"bars": {"AAPL": []}, "next_page_token": "repeat"},
    )

    with pytest.raises(AlpacaCatalogError, match="repeated a page token"):
        client.get_daily_bars(
            ["AAPL"], start=date(2026, 8, 27), end=date(2026, 8, 28)
        )


def test_alpaca_catalog_bounds_retry_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    _credentials(tmp_path)
    client = AlpacaCatalogClient(root=tmp_path)
    near_header = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    far_header = format_datetime(datetime.now(UTC) + timedelta(minutes=5), usegmt=True)

    near_delay = client._retry_delay(near_header, 0)
    far_delay = client._retry_delay(far_header, 0)

    assert 0 < near_delay <= 30
    assert 59 <= far_delay <= 60
    assert client._retry_delay("3600", 0) == 60
    assert client._retry_delay("not-a-retry-date", 2) == 4


def test_alpaca_catalog_uses_environment_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", " env-key ")
    monkeypatch.setenv("APCA_API_SECRET_KEY", " env-secret ")

    client = AlpacaCatalogClient(root=tmp_path)

    assert client._headers["APCA-API-KEY-ID"] == "env-key"
    assert client._headers["APCA-API-SECRET-KEY"] == "env-secret"


def test_alpaca_catalog_fails_cleanly_after_network_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    _credentials(tmp_path)
    attempts = 0

    def fail_urlopen(request: Request, timeout: int) -> io.BytesIO:
        nonlocal attempts
        attempts += 1
        raise URLError("offline")

    monkeypatch.setattr(alpaca_catalog, "urlopen", fail_urlopen)
    monkeypatch.setattr(alpaca_catalog.time, "sleep", lambda _: None)
    client = AlpacaCatalogClient(root=tmp_path)

    with pytest.raises(AlpacaCatalogError, match="request failed: offline"):
        client.list_active_us_assets()

    assert attempts == 4


def test_alpaca_catalog_rejects_malformed_provider_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    _credentials(tmp_path)
    client = AlpacaCatalogClient(root=tmp_path)

    monkeypatch.setattr(client, "_request_json", lambda _: {"unexpected": True})
    with pytest.raises(AlpacaCatalogError, match="assets response must be a list"):
        client.list_active_us_assets()

    monkeypatch.setattr(client, "_request_json", lambda _: [None, {"symbol": "AAPL"}])
    assert client.list_active_us_assets() == ()

    with pytest.raises(ValueError, match="batch_size"):
        client.get_daily_bars(
            ["AAPL"], start=date(2026, 8, 27), end=date(2026, 8, 28), batch_size=0
        )

    monkeypatch.setattr(client, "_request_json", lambda _: [])
    with pytest.raises(AlpacaCatalogError, match="bars response must be an object"):
        client.get_daily_bars(
            ["AAPL"], start=date(2026, 8, 27), end=date(2026, 8, 28)
        )

    monkeypatch.setattr(client, "_request_json", lambda _: {"bars": []})
    with pytest.raises(AlpacaCatalogError, match="bars payload is malformed"):
        client.get_daily_bars(
            ["AAPL"], start=date(2026, 8, 27), end=date(2026, 8, 28)
        )

    monkeypatch.setattr(
        client,
        "_request_json",
        lambda _: {
            "bars": {
                "OTHER": [],
                "AAPL": [None, {"t": "bad", "h": 1, "l": 1, "c": 1, "v": 1}],
            }
        },
    )
    assert client.get_daily_bars(
        ["AAPL"], start=date(2026, 8, 27), end=date(2026, 8, 28)
    ) == {"AAPL": ()}


def test_alpaca_catalog_requires_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)

    with pytest.raises(ValueError, match="credentials"):
        AlpacaCatalogClient(root=tmp_path)
