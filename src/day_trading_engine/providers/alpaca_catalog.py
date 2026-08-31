from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AlpacaCatalogError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AlpacaAsset:
    asset_id: str
    symbol: str
    name: str
    exchange: str
    status: str
    tradable: bool
    attributes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AlpacaDailyBar:
    session: date
    high: float
    low: float
    close: float
    volume: float


class AlpacaCatalogClient:
    """Small provider client for universe discovery; historical 1m backfill stays separate."""

    ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"
    BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
    feed = "sip"

    def __init__(self, *, root: Path) -> None:
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

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret_key,
        }

    def _request_json(self, url: str) -> object:
        request = Request(url, headers=self._headers)
        for attempt in range(4):
            try:
                with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed Alpaca HTTPS endpoints
                    return json.load(response)
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == 3:
                    raise AlpacaCatalogError(
                        f"Alpaca catalog API failed with HTTP {exc.code}"
                    ) from exc
                retry_after = exc.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else 2**attempt)
            except URLError as exc:
                if attempt == 3:
                    raise AlpacaCatalogError(
                        f"Alpaca catalog request failed: {exc.reason}"
                    ) from exc
                time.sleep(2**attempt)
        raise AlpacaCatalogError("Alpaca catalog request failed")

    def list_active_us_assets(self) -> tuple[AlpacaAsset, ...]:
        query = urlencode({"status": "active", "asset_class": "us_equity"})
        payload = self._request_json(f"{self.ASSETS_URL}?{query}")
        if not isinstance(payload, list):
            raise AlpacaCatalogError("Alpaca assets response must be a list")
        assets: list[AlpacaAsset] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                assets.append(
                    AlpacaAsset(
                        asset_id=str(item["id"]),
                        symbol=str(item["symbol"]).strip().upper(),
                        name=str(item.get("name", "")).strip(),
                        exchange=str(item.get("exchange", "")).strip().upper(),
                        status=str(item.get("status", "")).strip().lower(),
                        tradable=bool(item.get("tradable", False)),
                        attributes=tuple(str(value) for value in item.get("attributes", []) or []),
                    )
                )
            except KeyError:
                continue
        return tuple(assets)

    def get_daily_bars(
        self,
        symbols: list[str] | tuple[str, ...],
        *,
        start: date,
        end: date,
        batch_size: int = 200,
    ) -> dict[str, tuple[AlpacaDailyBar, ...]]:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        result: dict[str, list[AlpacaDailyBar]] = {symbol: [] for symbol in normalized}
        for offset in range(0, len(normalized), batch_size):
            batch = normalized[offset : offset + batch_size]
            page_token: str | None = None
            while True:
                params: dict[str, object] = {
                    "symbols": ",".join(batch),
                    "timeframe": "1Day",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "feed": self.feed,
                    "adjustment": "raw",
                    "limit": 10000,
                }
                if page_token:
                    params["page_token"] = page_token
                payload = self._request_json(f"{self.BARS_URL}?{urlencode(params)}")
                if not isinstance(payload, dict):
                    raise AlpacaCatalogError("Alpaca bars response must be an object")
                bars = payload.get("bars", {})
                if not isinstance(bars, dict):
                    raise AlpacaCatalogError("Alpaca bars payload is malformed")
                for symbol, rows in bars.items():
                    normalized_symbol = str(symbol).upper()
                    if normalized_symbol not in result or not isinstance(rows, list):
                        continue
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        try:
                            timestamp = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00"))
                            result[normalized_symbol].append(
                                AlpacaDailyBar(
                                    session=timestamp.astimezone(UTC).date(),
                                    high=float(row["h"]),
                                    low=float(row["l"]),
                                    close=float(row["c"]),
                                    volume=float(row["v"]),
                                )
                            )
                        except (KeyError, TypeError, ValueError):
                            continue
                token = payload.get("next_page_token")
                page_token = str(token) if token else None
                if not page_token:
                    break
        return {
            symbol: tuple(sorted(rows, key=lambda row: row.session))
            for symbol, rows in result.items()
        }
