from datetime import UTC, datetime, timedelta

from day_trading_engine.context import ContextRecord, ContextStore, collect_context


class Provider:
    name = "integration"

    def fetch(self, received_at: datetime) -> list[ContextRecord]:
        return [
            ContextRecord(
                kind="news",
                provider=self.name,
                external_id="story-1",
                title="Point in time story",
                source_at=received_at - timedelta(minutes=1),
                received_at=received_at,
            )
        ]


def test_collection_store_and_snapshot_are_point_in_time_safe(tmp_path) -> None:
    received_at = datetime(2026, 8, 24, 16, 0, tzinfo=UTC)
    result = collect_context([Provider()], received_at=received_at)
    with ContextStore(tmp_path / "context.db") as store:
        assert store.add_many(result.records) == 1
        assert store.as_of(received_at - timedelta(seconds=1)) == []
        assert [item.external_id for item in store.as_of(received_at)] == ["story-1"]
