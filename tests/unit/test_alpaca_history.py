from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request

from day_trading_engine.providers import alpaca_history
from day_trading_engine.providers.alpaca_history import AlpacaHistoryClient


def test_alpaca_history_maps_sip_bar_from_env_file(
    tmp_path: Path, monkeypatch: object
) -> None:
    (tmp_path / ".env").write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        captured.append(request)
        assert timeout == 30
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
    client = AlpacaHistoryClient({"AAPL": 8049}, root=tmp_path)
    batch = client.get_candles(
        8049,
        start=datetime(2026, 4, 1, 13, 30, tzinfo=UTC),
        end=datetime(2026, 4, 1, 20, 0, tzinfo=UTC),
    )

    assert len(batch.candles) == 1
    assert batch.candles[0].close == 254.17
    assert batch.candles[0].end == datetime(2026, 4, 1, 13, 31, tzinfo=UTC)
    assert "feed=sip" in captured[0].full_url
    assert captured[0].get_header("Apca-api-key-id") == "test-key"
