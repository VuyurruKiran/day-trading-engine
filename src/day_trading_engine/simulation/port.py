from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class SimulationResult:
    deterministic_id: str
    bars_processed: int
    final_close: float


class SimulationEngine(Protocol):
    """Replaceable replay/simulation boundary for M0."""

    def replay(self, symbol: str, bars: Sequence[Bar]) -> SimulationResult: ...
