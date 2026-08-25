from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlencode

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.providers._json_http import get_json

JsonFetcher = Callable[..., dict]


def _date(value: str, fallback: datetime) -> datetime:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return fallback


class FredSeriesProvider:
    name = "fred"

    def __init__(
        self,
        series_id: str,
        *,
        api_key: str,
        fetch_json: JsonFetcher = get_json,
    ) -> None:
        if not series_id.strip():
            raise ValueError("series_id is required")
        if not api_key.strip():
            raise ValueError("FRED api_key is required")
        self._series_id = series_id.strip().upper()
        self._api_key = api_key.strip()
        self._fetch_json = fetch_json

    def fetch(self, received_at: datetime) -> list[ContextRecord]:
        params = urlencode(
            {
                "series_id": self._series_id,
                "api_key": self._api_key,
                "file_type": "json",
            }
        )
        payload = self._fetch_json(
            f"https://api.stlouisfed.org/fred/series/observations?{params}"
        )
        records: list[ContextRecord] = []
        for observation in payload.get("observations", []):
            observation_date = str(observation.get("date") or "")
            realtime_start = str(observation.get("realtime_start") or "")
            external_id = f"{self._series_id}:{observation_date}:{realtime_start}"
            records.append(
                ContextRecord(
                    kind="macro",
                    provider=self.name,
                    external_id=external_id,
                    title=self._series_id,
                    source_at=_date(realtime_start, received_at),
                    received_at=received_at,
                    payload={
                        "series_id": self._series_id,
                        "observation_date": observation_date,
                        "value": observation.get("value"),
                        "realtime_start": realtime_start,
                        "realtime_end": observation.get("realtime_end"),
                    },
                )
            )
        return records
