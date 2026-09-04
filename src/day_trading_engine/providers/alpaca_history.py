from __future__ import annotations

import json
import os
import threading
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
    _MIN_REQUEST_INTERVAL_SECONDS = 0.31

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
        self._request_lock = threading.Lock()
        self._next_request_at = 0.0

    def _pace_request(self) -> None:
        """Keep concurrent callers below Alpaca Basic's 200-request/minute limit."""
        with self._request_lock:
            now = time.monotonic()
            delay = self._next_request_at - now
            if delay > 0:
                time.sleep(delay)
                now += delay
            self._next_request_at = now + self._MIN_REQUEST_INTERVAL_SECONDS

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
        bars = self._get_paginated(normalized, "bars", base_params)

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

    def missing_minutes_have_no_bar_eligible_trades(
        self, symbol: str, missing_minutes: tuple[str, ...]
    ) -> bool:
        """Confirm Alpaca omitted bars because no trade could populate minute OHLC."""
        normalized = symbol.strip().upper()
        if normalized not in self._symbols:
            raise ValueError(f"unknown symbol: {normalized}")
        if not missing_minutes:
            raise ValueError("missing minutes are required")
        targets = {
            datetime.fromisoformat(value).astimezone(UTC).replace(second=0, microsecond=0)
            for value in missing_minutes
        }
        params = {
            "start": min(targets).isoformat().replace("+00:00", "Z"),
            "end": (max(targets) + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "feed": self.feed,
            "limit": 10000,
            "sort": "asc",
        }
        for trade in self._get_paginated(normalized, "trades", params):
            timestamp = trade.get("t")
            if not isinstance(timestamp, str):
                return False
            minute = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).replace(
                second=0, microsecond=0
            )
            if minute in targets and _trade_can_create_minute_bar(trade):
                return False
        return True

    def _get_paginated(
        self, symbol: str, resource: str, base_params: dict[str, object]
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        page_token: str | None = None
        while True:
            params = {**base_params}
            if page_token:
                params["page_token"] = page_token
            request = Request(
                f"https://data.alpaca.markets/v2/stocks/{symbol}/{resource}?{urlencode(params)}",
                headers={
                    "APCA-API-KEY-ID": self._key_id,
                    "APCA-API-SECRET-KEY": self._secret_key,
                },
            )
            self._pace_request()
            max_attempts = 4
            for attempt in range(1, max_attempts + 1):
                try:
                    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS endpoint
                        payload = json.load(response)
                    break
                except HTTPError as exc:
                    retryable = exc.code == 429 or 500 <= exc.code < 600
                    if not retryable or attempt == max_attempts:
                        detail = _http_error_detail(exc)
                        raise AlpacaHistoryError(
                            f"Alpaca historical API failed with HTTP {exc.code}{detail}"
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

            if not isinstance(payload, dict):
                raise AlpacaHistoryError(f"Alpaca historical {resource} response is malformed")
            page_items = payload.get(resource)
            if page_items is None:
                page_items = []
            if not isinstance(page_items, list):
                raise AlpacaHistoryError(f"Alpaca historical {resource} response is malformed")
            items.extend(page_items)
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        return items


def _http_error_detail(exc: HTTPError) -> str:
    """Return only Alpaca's structured code/message, never arbitrary response content."""
    try:
        payload = json.loads(exc.read(4096))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return ""
    safe_message = " ".join(message.split())[:300]
    code = payload.get("code")
    prefix = f" {code}" if isinstance(code, int) else ""
    return f" ({prefix.strip() + ': ' if prefix else ''}{safe_message})"


_MINUTE_PRICE_EXCLUDING_CONDITIONS = frozenset(
    {"C", "G", "H", "I", "M", "N", "P", "Q", "R", "U", "V", "W", "Z", "4", "7", "9"}
)
_KNOWN_TRADE_CONDITIONS = frozenset(
    {
        " ",
        "@",
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
        "H",
        "I",
        "K",
        "L",
        "M",
        "N",
        "O",
        "P",
        "Q",
        "R",
        "T",
        "U",
        "V",
        "W",
        "X",
        "Y",
        "Z",
        "4",
        "5",
        "6",
        "7",
        "9",
    }
)


def _trade_can_create_minute_bar(trade: dict[str, object]) -> bool:
    """Fail closed on unknown metadata; only prove an omission for documented rules."""
    raw_conditions = trade.get("c", [])
    if not isinstance(raw_conditions, list) or any(
        not isinstance(value, str) for value in raw_conditions
    ):
        return True
    conditions = set(raw_conditions)
    if not conditions <= _KNOWN_TRADE_CONDITIONS:
        return True
    if conditions & _MINUTE_PRICE_EXCLUDING_CONDITIONS:
        return False
    if "B" in conditions and trade.get("z") in {"A", "B"}:
        return False
    size = trade.get("s")
    return not isinstance(size, (int, float)) or size > 0
