from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import urlencode

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.providers._json_http import get_json

JsonFetcher = Callable[..., dict]


def _parse_seen(value: str, fallback: datetime) -> datetime:
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    return fallback


class GdeltNewsProvider:
    name = "gdelt"

    def __init__(
        self,
        query: str,
        *,
        symbols: tuple[str, ...] = (),
        max_records: int = 50,
        fetch_json: JsonFetcher = get_json,
    ) -> None:
        if not query.strip():
            raise ValueError("query is required")
        if not 1 <= max_records <= 250:
            raise ValueError("max_records must be between 1 and 250")
        self._query = query
        self._symbols = symbols
        self._max_records = max_records
        self._fetch_json = fetch_json

    def fetch(self, received_at: datetime) -> list[ContextRecord]:
        params = urlencode(
            {
                "query": self._query,
                "mode": "ArtList",
                "maxrecords": self._max_records,
                "format": "json",
                "sort": "HybridRel",
            }
        )
        payload = self._fetch_json(f"https://api.gdeltproject.org/api/v2/doc/doc?{params}")
        records: list[ContextRecord] = []
        for article in payload.get("articles", []):
            title = str(article.get("title") or "").strip()
            if not title:
                continue
            url = str(article.get("url") or "").strip() or None
            external_id = url or sha256(title.encode("utf-8")).hexdigest()
            records.append(
                ContextRecord(
                    kind="news",
                    provider=self.name,
                    external_id=external_id,
                    title=title,
                    source_at=_parse_seen(str(article.get("seendate") or ""), received_at),
                    received_at=received_at,
                    symbols=self._symbols,
                    url=url,
                    payload=article,
                )
            )
        return records
