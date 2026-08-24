from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field


class QuestradeError(RuntimeError):
    """Base Questrade adapter error."""


class QuestradeAuthError(QuestradeError):
    """Authentication or token-refresh failure."""


class QuestradeApiError(QuestradeError):
    """Non-retryable Questrade API failure."""


class QuestradeNetworkError(QuestradeError):
    """Transient network failure while calling Questrade."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class Transport(Protocol):
    def request(self, method: str, url: str, headers: dict[str, str]) -> HttpResponse: ...


class UrllibTransport:
    def request(self, method: str, url: str, headers: dict[str, str]) -> HttpResponse:
        request = Request(url=url, method=method, headers=headers)
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310
                return HttpResponse(
                    status=response.status,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    body=response.read(),
                )
        except HTTPError as exc:
            return HttpResponse(
                status=exc.code,
                headers={k.lower(): v for k, v in exc.headers.items()},
                body=exc.read(),
            )
        except URLError as exc:
            raise QuestradeNetworkError(f"Questrade network error: {exc.reason}") from exc


class Quote(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    symbol: str
    symbolId: int
    bidPrice: float | None = None
    bidSize: int | None = None
    askPrice: float | None = None
    askSize: int | None = None
    lastTradePrice: float | None = None
    volume: int | None = None
    openPrice: float | None = None
    highPrice: float | None = None
    lowPrice: float | None = None
    delay: int = Field(default=0, ge=0)
    isHalted: bool = False


class SymbolMatch(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    symbol: str
    symbolId: int
    isQuotable: bool = True
    isTradable: bool = True


class Market(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    tradingVenues: list[str] = []
    defaultTradingVenue: str | None = None
    primaryOrderRoutes: list[str] = []
    secondaryOrderRoutes: list[str] = []
    level1Feeds: list[str] = []
    level2Feeds: list[str] = []
    extendedStartTime: str | None = None
    startTime: str | None = None
    endTime: str | None = None
    extendedEndTime: str | None = None
    snapQuotesLimit: int | None = None


@dataclass(frozen=True)
class ResponseMeta:
    source_at: datetime
    received_at: datetime
    source_time_origin: str
    latency_ms: int
    rate_limit_remaining: int | None
    rate_limit_reset: int | None


@dataclass(frozen=True)
class QuoteBatch:
    quotes: tuple[Quote, ...]
    meta: ResponseMeta


@dataclass
class TokenState:
    access_token: str
    refresh_token: str
    api_server: str
    expires_at: datetime

    @property
    def expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at - timedelta(seconds=30)


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> TokenState | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return TokenState(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            api_server=payload["api_server"],
            expires_at=datetime.fromisoformat(payload["expires_at"]),
        )

    def save(self, state: TokenState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": state.access_token,
            "refresh_token": state.refresh_token,
            "api_server": state.api_server,
            "expires_at": state.expires_at.isoformat(),
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


class QuestradeClient:
    AUTH_URL = "https://login.questrade.com/oauth2/token"

    def __init__(
        self,
        refresh_token: str,
        token_store: TokenStore,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ) -> None:
        if not refresh_token.strip():
            raise QuestradeAuthError("QUESTRADE_REFRESH_TOKEN is required")
        self._bootstrap_refresh_token = refresh_token.strip()
        self._token_store = token_store
        self._transport = transport or UrllibTransport()
        self._sleep = sleep
        self._max_retries = max_retries
        self._tokens = token_store.load()

    def authenticate(self, force: bool = False) -> TokenState:
        if self._tokens and not force and not self._tokens.expired:
            return self._tokens

        refresh_token = (
            self._tokens.refresh_token
            if self._tokens is not None
            else self._bootstrap_refresh_token
        )
        query = urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token})
        response: HttpResponse | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._transport.request("GET", f"{self.AUTH_URL}?{query}", {})
            except QuestradeNetworkError:
                if attempt >= self._max_retries:
                    raise
                self._sleep(self._backoff_delay(attempt))
                continue
            if response.status == 429 or 500 <= response.status < 600:
                if attempt < self._max_retries:
                    self._sleep(self._retry_delay(response, attempt))
                    continue
            break

        if response is None or response.status != 200:
            status = response.status if response is not None else "network"
            raise QuestradeAuthError(f"Questrade token refresh failed with HTTP {status}")

        payload = self._json(response)
        try:
            self._tokens = TokenState(
                access_token=str(payload["access_token"]),
                refresh_token=str(payload["refresh_token"]),
                api_server=str(payload["api_server"]).rstrip("/") + "/",
                expires_at=datetime.now(UTC) + timedelta(seconds=int(payload["expires_in"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise QuestradeAuthError("Questrade token response is malformed") from exc
        self._token_store.save(self._tokens)
        return self._tokens

    def get_markets(self) -> tuple[Market, ...]:
        payload, _ = self._get_json("markets")
        return tuple(Market.model_validate(item) for item in payload.get("markets", []))

    def resolve_symbol(self, symbol: str) -> SymbolMatch:
        normalized = symbol.strip().upper()
        payload, _ = self._get_json("symbols/search", {"prefix": normalized})
        matches = [SymbolMatch.model_validate(item) for item in payload.get("symbols", [])]
        for match in matches:
            if match.symbol.upper() == normalized and match.isQuotable:
                return match
        raise QuestradeApiError(f"No quotable Questrade symbol found for {normalized}")

    def get_quotes(self, symbol_ids: list[int], batch_size: int = 50) -> tuple[QuoteBatch, ...]:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if not symbol_ids:
            return ()

        batches: list[QuoteBatch] = []
        for start in range(0, len(symbol_ids), batch_size):
            ids = symbol_ids[start : start + batch_size]
            payload, meta = self._get_json("markets/quotes", {"ids": ",".join(map(str, ids))})
            quotes = tuple(Quote.model_validate(item) for item in payload.get("quotes", []))
            batches.append(QuoteBatch(quotes=quotes, meta=meta))
        return tuple(batches)

    def _get_json(
        self, endpoint: str, params: dict[str, str] | None = None
    ) -> tuple[dict[str, object], ResponseMeta]:
        last_status = 0
        for attempt in range(self._max_retries + 1):
            tokens = self.authenticate()
            query = f"?{urlencode(params)}" if params else ""
            url = f"{tokens.api_server}v1/{endpoint}{query}"
            try:
                response = self._transport.request(
                    "GET", url, {"Authorization": f"Bearer {tokens.access_token}"}
                )
            except QuestradeNetworkError:
                if attempt >= self._max_retries:
                    raise
                self._sleep(self._backoff_delay(attempt))
                continue
            received_at = datetime.now(UTC)
            last_status = response.status

            if response.status == 401 and attempt == 0:
                self.authenticate(force=True)
                continue
            if response.status == 429 or 500 <= response.status < 600:
                if attempt < self._max_retries:
                    self._sleep(self._retry_delay(response, attempt))
                    continue
            if response.status != 200:
                message = f"Questrade API {endpoint} failed with HTTP {response.status}"
                raise QuestradeApiError(message)

            payload = self._json(response)
            return payload, self._response_meta(response, received_at)

        raise QuestradeApiError(f"Questrade API {endpoint} failed with HTTP {last_status}")

    @staticmethod
    def _json(response: HttpResponse) -> dict[str, object]:
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QuestradeApiError("Questrade returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise QuestradeApiError("Questrade JSON response must be an object")
        return payload

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        return min(8.0, 0.5 * (2**attempt))

    @staticmethod
    def _retry_delay(response: HttpResponse, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return QuestradeClient._backoff_delay(attempt)

    @staticmethod
    def _response_meta(response: HttpResponse, received_at: datetime) -> ResponseMeta:
        date_header = response.headers.get("date")
        origin = "received_proxy"
        source_at = received_at
        if date_header:
            try:
                source_at = parsedate_to_datetime(date_header).astimezone(UTC)
                origin = "http_date"
            except (TypeError, ValueError):
                pass
        latency_ms = max(0, int((received_at - source_at).total_seconds() * 1000))
        return ResponseMeta(
            source_at=source_at,
            received_at=received_at,
            source_time_origin=origin,
            latency_ms=latency_ms,
            rate_limit_remaining=QuestradeClient._header_int(
                response.headers, "x-ratelimit-remaining"
            ),
            rate_limit_reset=QuestradeClient._header_int(response.headers, "x-ratelimit-reset"),
        )

    @staticmethod
    def _header_int(headers: dict[str, str], key: str) -> int | None:
        value = headers.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None
