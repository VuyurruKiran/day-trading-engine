from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from day_trading_engine.features.market import build_market_features


@dataclass(frozen=True)
class ReplayFrame:
    as_of: datetime
    features: pd.DataFrame


class HistoricalReplay:
    def __init__(self, samples: pd.DataFrame) -> None:
        frame = samples.copy()
        if "received_at" not in frame.columns:
            raise ValueError("received_at is required")
        frame["received_at"] = pd.to_datetime(frame["received_at"], utc=True, errors="raise")
        self._samples = frame.sort_values("received_at", kind="stable").reset_index(drop=True)

    def replay(self, *, previous_close: float | None = None) -> list[ReplayFrame]:
        # Ponytail: prefix recomputation is intentionally O(n²) for small M3 replay datasets.
        # Replace with incremental feature state only when replay volume proves this is a bottleneck.
        results: list[ReplayFrame] = []
        for timestamp in self._samples["received_at"].drop_duplicates():
            as_of = timestamp.to_pydatetime()
            features = build_market_features(
                self._samples,
                as_of=as_of,
                previous_close=previous_close,
            )
            results.append(ReplayFrame(as_of=as_of, features=features))
        return results
