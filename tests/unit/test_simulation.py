from datetime import UTC, datetime, timedelta

import pytest

from day_trading_engine.simulation.port import Bar
from day_trading_engine.simulation.reference import ReferenceSimulationEngine


def sample_bars() -> list[Bar]:
    start = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    return [
        Bar(start, 10.0, 10.4, 9.9, 10.2, 1000),
        Bar(start + timedelta(minutes=1), 10.2, 10.5, 10.1, 10.4, 1200),
    ]


def test_replay_is_deterministic() -> None:
    engine = ReferenceSimulationEngine()
    first = engine.replay("ABC", sample_bars())
    second = engine.replay("ABC", list(reversed(sample_bars())))
    assert first == second
    assert first.bars_processed == 2
    assert first.final_close == 10.4


def test_symbol_normalization_is_deterministic() -> None:
    engine = ReferenceSimulationEngine()
    assert engine.replay("abc", sample_bars()).deterministic_id == engine.replay(
        "ABC", sample_bars()
    ).deterministic_id


def test_duplicate_timestamps_are_rejected() -> None:
    engine = ReferenceSimulationEngine()
    first = sample_bars()[0]
    duplicate = Bar(first.ts, 11.0, 11.2, 10.9, 11.1, 900)
    with pytest.raises(ValueError, match="duplicate bar timestamps"):
        engine.replay("ABC", [first, duplicate])
