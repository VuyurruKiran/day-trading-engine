from __future__ import annotations

import json
from collections import deque
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from day_trading_engine.market_data.backfill import backfill_one_minute_history
from day_trading_engine.providers.questrade import (
    HttpResponse,
    QuestradeApiError,
    QuestradeClient,
    TokenStore,
)
from day_trading_engine.providers.questrade_history import (
    HistoricalCandle,
    QuestradeHistoryClient,
)


class InclusiveHistoryClient:
    def get_candles(self, symbol_id: int, **kwargs: object) -> SimpleNamespace:
        start = kwargs["start"]
        end = kwargs["end"]
        assert isinstance(start, datetime)
        assert isinstance(end, datetime)
        candles: list[HistoricalCandle] = []
        current = start
        while current <= end:
            candles.append(
                HistoricalCandle(
                    start=current,
                    end=current + timedelta(minutes=1),
                    open=10,
                    high=11,
                    low=9,
                    close=10.5,
                    volume=100,
                )
            )
            current += timedelta(minutes=1)
        return SimpleNamespace(candles=tuple(candles))


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = deque(responses)

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        return self.responses.popleft()


def _response(status: int, payload: dict[str, object]) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"date": "Tue, 25 Aug 2026 18:00:00 GMT"},
        body=json.dumps(payload).encode(),
    )


def test_backfill_accepts_questrade_inclusive_close_candle(tmp_path: Path) -> None:
    manifest = backfill_one_minute_history(
        InclusiveHistoryClient(),
        symbols={"AAPL": 8049},
        start=date(2026, 7, 31),
        end=date(2026, 7, 31),
        root=tmp_path,
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entry = payload["entries"][0]
    assert entry["status"] == "complete"
    assert entry["rows"] == 390
    assert payload["coverage"]["current_request_complete"] is True


def test_questrade_api_error_includes_provider_code_and_message(tmp_path: Path) -> None:
    transport = FakeTransport(
        [
            _response(
                200,
                {
                    "access_token": "access",
                    "refresh_token": "rotated",
                    "api_server": "https://api01.iq.questrade.com/",
                    "expires_in": 1800,
                },
            ),
            _response(400, {"code": 1017, "message": "Historical data unavailable"}),
        ]
    )
    client = QuestradeHistoryClient(
        "refresh",
        TokenStore(tmp_path / "token.json"),
        transport=transport,
    )

    with pytest.raises(
        QuestradeApiError,
        match="HTTP 400: 1017: Historical data unavailable",
    ):
        client.get_candles(
            8049,
            start=datetime(2026, 6, 24, 13, 30, tzinfo=UTC),
            end=datetime(2026, 6, 24, 20, 0, tzinfo=UTC),
        )


def test_questrade_error_detail_caps_combined_code_and_message() -> None:
    detail = QuestradeClient._safe_error_detail(
        _response(400, {"code": 1017, "message": "x" * 250})
    )

    assert len(detail) == 200
    assert detail.startswith("1017: ")
