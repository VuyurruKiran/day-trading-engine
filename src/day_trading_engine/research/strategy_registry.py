from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class StrategyEvidence:
    strategy_id: str
    source: str
    license: str | None
    hypothesis: str
    required_data: tuple[str, ...]
    parameters: dict[str, object]
    anti_leakage_review: str
    reproduction_status: str
    out_of_sample_metrics: dict[str, float]
    sensitivity: str


class StrategyRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS strategy_registry "
                "(strategy_id TEXT PRIMARY KEY, record TEXT NOT NULL)"
            )

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def put(self, record: StrategyEvidence) -> None:
        payload = asdict(record)
        payload["required_data"] = list(record.required_data)
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO strategy_registry VALUES (?, ?)",
                (record.strategy_id, json.dumps(payload, sort_keys=True)),
            )

    def get(self, strategy_id: str) -> StrategyEvidence:
        with self._db() as db:
            row = db.execute(
                "SELECT record FROM strategy_registry WHERE strategy_id = ?", (strategy_id,)
            ).fetchone()
        if row is None:
            raise KeyError(strategy_id)
        payload = json.loads(row[0])
        payload["required_data"] = tuple(payload["required_data"])
        return StrategyEvidence(**payload)
