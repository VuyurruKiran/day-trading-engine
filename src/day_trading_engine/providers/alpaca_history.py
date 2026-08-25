from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from day_trading_engine.providers.questrade_history import HistoricalCandle


class AlpacaHistoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlpacaHistoricalCandleBatch:
    candles: tuple[HistoricalCandle, ...]


class AlpacaHistoryClient:
    provider = "alpaca"
    feed = "sip"

    def __init__(self, symbols: dict[str, int], *, root: Path) -> None:
        if len(set(symbols.values())) != len(symbols):
            raise ValueError("symbol ids must be unique")
        self._symbols = {symbol_id: symbol.upper() for symbol, symbol_id in symbols.items()}
        self._key_id = self._load_secret(root, "APCA_API_KEY_ID")
        self._secret_key = self._load_secret(root, "APCA_API_SECRET_KEY")
        if not self._key_id or not self._secret_key:
            raise ValueError("Alpaca API credentials are required")

    @staticmethod
    def _load_secret(root: Path, key: str) -> str:
        value = os.getenv(key, "").strip()
        if value:
            return value
        env_file = root / ".env"
        if env_file.exists():
            for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() == key:
                    return value.strip().strip('"').strip("'")
        return ""

    def get_candles(
        self,
        symbol_id: int,
        *,
        start: datetime,
        end: datetime,
        interval: str = "OneMinute",
    ) -> AlpacaHistoricalCandleBatch:
        if interval != "OneMinute":
            raise ValueError("Alpaca historical backfill currently supports OneMinute only")
        symbol = self._symbols.get(symbol_id)
        if symbol is None:
            raise ValueError(f"unknown symbol id: {symbol_id}")

        base_params = {
            "timeframe": "1Min",
            "start": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "feed": self.feed,
            "adjustment": "raw",
            "limit": 10000,
        }
        bars: list[dict[str, object]] = []
        page_token: str | None = None
        while True:
            params = {**base_params}
            if page_token:
                params["page_token"] = page_token
            request = Request(
                f"https://data.alpaca.markets/v2/stocks/{symbol}/bars?{urlencode(params)}",
                headers={
                    "APCA-API-KEY-ID": self._key_id,
                    "APCA-API-SECRET-KEY": self._secret_key,
                },
            )
            try:
                with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS endpoint
                    payload = json.load(response)
            except HTTPError as exc:
                raise AlpacaHistoryError(
                    f"Alpaca historical API failed with HTTP {exc.code}"
                ) from exc
            except URLError as exc:
                raise AlpacaHistoryError(
                    f"Alpaca historical API request failed: {exc.reason}"
                ) from exc

            bars.extend(payload.get("bars", []))
            page_token = payload.get("next_page_token")
            if not page_token:
                break

        candles = tuple(
            HistoricalCandle(
                start=datetime.fromisoformat(item["t"].replace("Z", "+00:00")),
                end=datetime.fromisoformat(item["t"].replace("Z", "+00:00"))
                + timedelta(minutes=1),
                open=item["o"],
                high=item["h"],
                low=item["l"],
                close=item["c"],
                volume=item["v"],
            )
            for item in bars
        )
        return AlpacaHistoricalCandleBatch(candles=candles)
