from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from day_trading_engine.providers.questrade import (
    HttpResponse,
    QuestradeClient,
    TokenState,
    TokenStore,
)


class DetailTransport:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        self.urls.append(url)
        payload = {
            "symbols": [
                {
                    "symbol": "AAPL",
                    "symbolId": 8049,
                    "listingExchange": "NASDAQ",
                    "securityType": "Stock",
                    "isQuotable": True,
                    "isTradable": True,
                    "currency": "USD",
                    "industrySector": "Technology",
                },
                {
                    "symbol": "F",
                    "symbolId": 1234,
                    "listingExchange": "NYSE",
                    "securityType": "Stock",
                    "isQuotable": True,
                    "isTradable": True,
                    "currency": "USD",
                    "industrySector": "ConsumerCyclical",
                },
            ]
        }
        return HttpResponse(200, {}, json.dumps(payload).encode())


def test_questrade_get_symbol_details_batches_names(tmp_path: Path) -> None:
    token_store = TokenStore(tmp_path / "tokens.json")
    token_store.save(
        TokenState(
            access_token="access",
            refresh_token="refresh",
            api_server="https://api.example/",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    transport = DetailTransport()
    client = QuestradeClient("bootstrap", token_store, transport=transport)

    details = client.get_symbol_details(["aapl", "F", "AAPL"], batch_size=50)

    assert [detail.symbol for detail in details] == ["AAPL", "F"]
    assert details[0].industrySector == "Technology"
    assert "v1/symbols?names=AAPL%2CF" in transport.urls[0]
