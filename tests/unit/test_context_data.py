from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from day_trading_engine.context import ContextRecord, ContextStore, collect_context
from day_trading_engine.providers.fred import FredSeriesProvider
from day_trading_engine.providers.gdelt import GdeltNewsProvider
from day_trading_engine.providers.sec import SecFilingsProvider

NOW = datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc)


def record(**overrides: object) -> ContextRecord:
    values = {
        "kind": "news",
        "provider": "test",
        "external_id": "1",
        "title": "Same headline",
        "source_at": NOW - timedelta(minutes=5),
        "received_at": NOW,
        "symbols": ("aapl", "AAPL", "msft"),
        "payload": {"x": 1},
    }
    values.update(overrides)
    return ContextRecord(**values)  # type: ignore[arg-type]


def test_record_requires_timezone_aware_timestamps() -> None:
    with pytest.raises(ValueError, match="source_at must be timezone-aware"):
        record(source_at=NOW.replace(tzinfo=None))


def test_record_normalizes_symbols_and_news_dedupe_key() -> None:
    first = record()
    second = record(provider="other", external_id="2", title="same---HEADLINE!!")
    assert first.symbols == ("AAPL", "MSFT")
    assert first.dedupe_key == second.dedupe_key


def test_store_deduplicates_syndicated_news_and_filters_by_received_time(tmp_path) -> None:
    early = record(external_id="early", received_at=NOW - timedelta(minutes=10))
    duplicate = record(provider="wire-copy", external_id="copy")
    late = record(external_id="late", title="Different headline", received_at=NOW + timedelta(minutes=1))
    with ContextStore(tmp_path / "context.db") as store:
        assert store.add_many([early, duplicate, late]) == 2
        snapshot = store.as_of(NOW)
    assert [item.external_id for item in snapshot] == ["early"]


def test_store_requires_aware_snapshot_cutoff(tmp_path) -> None:
    with ContextStore(tmp_path / "context.db") as store:
        with pytest.raises(ValueError, match="cutoff must be timezone-aware"):
            store.as_of(NOW.replace(tzinfo=None))


def test_collection_isolates_provider_failure() -> None:
    class Good:
        name = "good"

        def fetch(self, received_at: datetime) -> list[ContextRecord]:
            return [record(received_at=received_at)]

    class Broken:
        name = "broken"

        def fetch(self, received_at: datetime) -> list[ContextRecord]:
            raise RuntimeError("offline")

    result = collect_context([Broken(), Good()], received_at=NOW)
    assert len(result.records) == 1
    assert result.errors == ("broken: offline",)


def test_gdelt_normalizes_article() -> None:
    def fetch_json(url: str, **_: object) -> dict:
        assert "mode=ArtList" in url
        return {
            "articles": [
                {
                    "url": "https://example.com/story",
                    "title": "Chip export rules tighten",
                    "seendate": "20260824T155500Z",
                    "domain": "example.com",
                }
            ]
        }

    item = GdeltNewsProvider("semiconductors", symbols=("AMD",), fetch_json=fetch_json).fetch(NOW)[0]
    assert item.kind == "news"
    assert item.symbols == ("AMD",)
    assert item.source_at == datetime(2026, 8, 24, 15, 55, tzinfo=timezone.utc)


def test_sec_normalizes_recent_filings_and_filters_forms() -> None:
    def fetch_json(url: str, **kwargs: object) -> dict:
        assert url.endswith("CIK0000320193.json")
        assert kwargs["headers"] == {"User-Agent": "Trading Engine test@example.com"}
        return {
            "tickers": ["AAPL"],
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
                    "form": ["8-K", "4"],
                    "acceptanceDateTime": ["2026-08-24T15:30:00Z", "2026-08-24T15:31:00Z"],
                    "filingDate": ["2026-08-24", "2026-08-24"],
                    "reportDate": ["2026-08-24", "2026-08-24"],
                    "primaryDocument": ["a8k.htm", "form4.xml"],
                }
            },
        }

    items = SecFilingsProvider(
        320193,
        user_agent="Trading Engine test@example.com",
        forms=("8-K",),
        fetch_json=fetch_json,
    ).fetch(NOW)
    assert len(items) == 1
    assert items[0].symbols == ("AAPL",)
    assert items[0].payload["form"] == "8-K"


def test_fred_normalizes_vintage_metadata() -> None:
    def fetch_json(url: str, **_: object) -> dict:
        assert "series_id=CPIAUCSL" in url
        assert "file_type=json" in url
        return {
            "observations": [
                {
                    "realtime_start": "2026-08-24",
                    "realtime_end": "2026-08-24",
                    "date": "2026-07-01",
                    "value": "310.1",
                }
            ]
        }

    item = FredSeriesProvider("cpiaucsl", api_key="x" * 32, fetch_json=fetch_json).fetch(NOW)[0]
    assert item.kind == "macro"
    assert item.source_at == datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert item.payload["observation_date"] == "2026-07-01"
