from datetime import UTC, datetime
from pathlib import Path
from time import sleep

from day_trading_engine.context.collector import CollectionResult, collect_context
from day_trading_engine.context.models import ContextRecord
from day_trading_engine.core.config import load_config
from day_trading_engine.engine.live import _decision_time_reached, _refresh_context

ROOT = Path(__file__).resolve().parents[2]


def test_decision_time_gate_uses_configured_timezone() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    assert not _decision_time_reached(config, datetime(2026, 8, 29, 15, 59, tzinfo=UTC))
    assert _decision_time_reached(config, datetime(2026, 8, 29, 16, 0, tzinfo=UTC))


def test_context_without_override_is_timestamped_after_provider_returns() -> None:
    class Provider:
        name = "test"
        started_at: datetime | None = None

        def fetch(self, received_at: datetime) -> list[ContextRecord]:
            self.started_at = received_at
            sleep(0.001)
            return [
                ContextRecord(
                    kind="news",
                    provider="test",
                    external_id="one",
                    title="AAPL beats estimates",
                    source_at=received_at,
                    received_at=received_at,
                    symbols=("AAPL",),
                )
            ]

    provider = Provider()
    result = collect_context((provider,))
    assert provider.started_at is not None
    assert result.records[0].received_at > provider.started_at


def test_refresh_context_returns_post_collection_decision_time(
    tmp_path: Path, monkeypatch
) -> None:
    before = datetime.now(UTC)
    record = ContextRecord(
        kind="news",
        provider="test",
        external_id="one",
        title="AAPL beats estimates",
        source_at=before,
        received_at=before,
        symbols=("AAPL",),
    )
    monkeypatch.setattr(
        "day_trading_engine.engine.live.collect_public_context",
        lambda symbols: CollectionResult((record,), ()),
    )
    (tmp_path / "data").mkdir()

    added, completed_at = _refresh_context(
        tmp_path,
        ("AAPL",),
        software_version="0.1.0",
    )

    assert added == 1
    assert completed_at >= before
