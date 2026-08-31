from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import day_trading_engine.context.collector as collector
from day_trading_engine.context.models import ContextRecord
from day_trading_engine.providers.fred import FredSeriesProvider
from day_trading_engine.providers.sec import SecFilingsProvider

NOW = datetime(2026, 8, 31, 15, 0, tzinfo=UTC)


def test_fred_provider_builds_point_in_time_macro_records() -> None:
    provider = FredSeriesProvider(
        "vixcls",
        api_key="key",
        fetch_json=lambda *_args, **_kwargs: {
            "observations": [
                {
                    "date": "2026-08-28",
                    "value": "14.2",
                    "realtime_start": "2026-08-29",
                    "realtime_end": "2026-08-29",
                }
            ]
        },
    )
    rows = provider.fetch(NOW)
    assert len(rows) == 1
    assert rows[0].kind == "macro"
    assert rows[0].external_id == "VIXCLS:2026-08-28:2026-08-29"
    assert rows[0].source_at == datetime(2026, 8, 29, tzinfo=UTC)
    assert rows[0].payload["value"] == "14.2"

    with pytest.raises(ValueError, match="series_id"):
        FredSeriesProvider(" ", api_key="key")
    with pytest.raises(ValueError, match="api_key"):
        FredSeriesProvider("VIXCLS", api_key=" ")


def test_sec_provider_filters_forms_and_preserves_security_identity() -> None:
    provider = SecFilingsProvider(
        "320193",
        user_agent="tester test@example.com",
        forms=("8-K",),
        fetch_json=lambda *_args, **_kwargs: {
            "tickers": ["AAPL"],
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000001", "skip"],
                    "form": ["8-K", "4"],
                    "primaryDocument": ["aapl8k.htm", "form4.xml"],
                    "acceptanceDateTime": ["2026-08-28T17:00:00Z", ""],
                    "filingDate": ["2026-08-28", "2026-08-28"],
                    "reportDate": ["2026-08-28", "2026-08-28"],
                }
            },
        },
    )
    rows = provider.fetch(NOW)
    assert len(rows) == 1
    assert rows[0].kind == "filing"
    assert rows[0].symbols == ("AAPL",)
    assert rows[0].payload["form"] == "8-K"
    assert rows[0].url and rows[0].url.endswith("/aapl8k.htm")

    with pytest.raises(ValueError, match="cik"):
        SecFilingsProvider("bad", user_agent="agent")
    with pytest.raises(ValueError, match="user_agent"):
        SecFilingsProvider("1", user_agent=" ")


def test_optional_provider_configuration_is_explicit(monkeypatch) -> None:
    for name in ("FRED_API_KEY", "FRED_SERIES", "SEC_USER_AGENT", "SEC_CIK_MAP"):
        monkeypatch.delenv(name, raising=False)
    providers, errors = collector._optional_providers(("AAPL", "MSFT"))
    assert providers == ()
    assert len(errors) == 2

    monkeypatch.setenv("FRED_API_KEY", "key")
    monkeypatch.setenv("FRED_SERIES", "VIXCLS,DGS10")
    monkeypatch.setenv("SEC_USER_AGENT", "tester test@example.com")
    monkeypatch.setenv("SEC_CIK_MAP", '{"AAPL":"320193"}')
    providers, errors = collector._optional_providers(("AAPL", "MSFT"))
    assert len(providers) == 3
    assert errors == ("sec: CIK missing for 1 selected symbols",)

    monkeypatch.setenv("SEC_CIK_MAP", "not-json")
    _, errors = collector._optional_providers(("AAPL",))
    assert "invalid JSON" in errors[0]

    monkeypatch.setenv("SEC_CIK_MAP", "[]")
    _, errors = collector._optional_providers(("AAPL",))
    assert "JSON object" in errors[0]


def test_collect_context_isolates_provider_failure_and_stamps_completion() -> None:
    class Good:
        name = "good"

        def fetch(self, received_at):
            return [
                ContextRecord(
                    kind="news",
                    provider=self.name,
                    external_id="1",
                    title="AAPL update",
                    source_at=NOW,
                    received_at=received_at,
                    symbols=("AAPL",),
                )
            ]

    class Bad:
        name = "bad"

        def fetch(self, received_at):
            raise RuntimeError("offline")

    result = collector.collect_context((Good(), Bad()))
    assert len(result.records) == 1
    assert result.records[0].received_at >= NOW
    assert result.errors == ("bad: offline",)

    with pytest.raises(ValueError, match="timezone-aware"):
        collector.collect_context((Good(),), received_at=datetime(2026, 8, 31))
    assert collector.collect_context((), received_at=NOW).records == ()


def test_merge_news_associations_preserves_non_news_and_latest_record() -> None:
    filing = ContextRecord(
        kind="filing",
        provider="sec",
        external_id="filing-1",
        title="8-K filing",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
    )
    first = ContextRecord(
        kind="news",
        provider="one",
        external_id="old",
        title="Apple launches product",
        source_at=NOW,
        received_at=NOW,
        symbols=("AAPL",),
    )
    latest = ContextRecord(
        kind="news",
        provider="two",
        external_id="new",
        title="Apple launches product!",
        source_at=NOW + timedelta(minutes=1),
        received_at=NOW + timedelta(minutes=1),
        symbols=("MSFT",),
    )

    merged = collector._merge_news_associations((filing, first, latest))

    assert merged[0] == filing
    assert len(merged) == 2
    assert merged[1].external_id == "new"
    assert merged[1].symbols == ("AAPL", "MSFT")
    assert "NYSE" in collector._gdelt_security_query("aapl")
    with pytest.raises(ValueError, match="symbol"):
        collector._gdelt_security_query(" ")


def test_collect_public_context_normalizes_symbols_and_combines_errors(monkeypatch) -> None:
    seen: dict[str, object] = {}
    optional = SimpleNamespace(name="fred")

    monkeypatch.setattr(
        collector,
        "_optional_providers",
        lambda symbols: ((optional,), (f"config:{','.join(symbols)}",)),
    )
    monkeypatch.setattr(
        collector,
        "RedditProvider",
        lambda *args, **kwargs: SimpleNamespace(name="reddit", args=args, kwargs=kwargs),
    )
    monkeypatch.setattr(
        collector,
        "GdeltNewsProvider",
        lambda *args, **kwargs: SimpleNamespace(name="gdelt", args=args, kwargs=kwargs),
    )

    def fake_collect(providers, *, received_at=None):
        seen["providers"] = tuple(provider.name for provider in providers)
        seen["received_at"] = received_at
        return collector.CollectionResult((), ("provider:offline",))

    monkeypatch.setattr(collector, "collect_context", fake_collect)
    result = collector.collect_public_context(
        [" aapl ", "AAPL", "msft"],
        received_at=NOW,
    )

    assert seen["providers"] == ("reddit", "gdelt", "gdelt", "fred")
    assert seen["received_at"] == NOW
    assert result.errors == ("config:AAPL,MSFT", "provider:offline")
    with pytest.raises(ValueError, match="at least one"):
        collector.collect_public_context([" "])
