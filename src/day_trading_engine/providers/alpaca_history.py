from __future__ import annotations

import json
import os
import time
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

    def __init__(self, symbols: list[str] | tuple[str, ...], *, root: Path) -> None:
        normalized = [symbol.strip().upper() for symbol in symbols]
        if not normalized:
            raise ValueError("symbols are required")
        if any(not symbol for symbol in normalized):
            raise ValueError("symbols must be non-empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate symbol")
        self._symbols = set(normalized)
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
        symbol: str,
        *,
        start: datetime,
        end: datetime,
        interval: str = "OneMinute",
    ) -> AlpacaHistoricalCandleBatch:
        if interval != "OneMinute":
            raise ValueError("Alpaca historical backfill currently supports OneMinute only")
        normalized = symbol.strip().upper()
        if normalized not in self._symbols:
            raise ValueError(f"unknown symbol: {normalized}")

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
                f"https://data.alpaca.markets/v2/stocks/{normalized}/bars?{urlencode(params)}",
                headers={
                    "APCA-API-KEY-ID": self._key_id,
                    "APCA-API-SECRET-KEY": self._secret_key,
                },
            )
            max_attempts = 4
            for attempt in range(1, max_attempts + 1):
                try:
                    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS endpoint
                        payload = json.load(response)
                    break
                except HTTPError as exc:
                    retryable = exc.code == 429 or 500 <= exc.code < 600
                    if not retryable or attempt == max_attempts:
                        raise AlpacaHistoryError(
                            f"Alpaca historical API failed with HTTP {exc.code}"
                        ) from exc
                    retry_after = exc.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 2 ** (attempt - 1)
                    time.sleep(delay)
                except URLError as exc:
                    if attempt == max_attempts:
                        raise AlpacaHistoryError(
                            f"Alpaca historical API request failed: {exc.reason}"
                        ) from exc
                    time.sleep(2 ** (attempt - 1))

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
