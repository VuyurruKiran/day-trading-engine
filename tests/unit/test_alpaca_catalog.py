from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.request import Request

import pytest

from day_trading_engine.providers import alpaca_catalog
from day_trading_engine.providers.alpaca_catalog import AlpacaCatalogClient


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


def test_alpaca_catalog_parses_http_date_retry_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    _credentials(tmp_path)
    client = AlpacaCatalogClient(root=tmp_path)
    header = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)

    delay = client._retry_delay(header, 0)

    assert 0 < delay <= 30


def test_alpaca_catalog_requires_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)

    with pytest.raises(ValueError, match="credentials"):
        AlpacaCatalogClient(root=tmp_path)
