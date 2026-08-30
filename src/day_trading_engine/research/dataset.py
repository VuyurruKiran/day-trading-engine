from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from day_trading_engine.engine.domain import CohortBucket
from day_trading_engine.paper.replay import ReplayFidelity, ShadowOutcome


@dataclass(frozen=True)
class DecisionSnapshot:
    session: str
    symbol: str
    snapshot_at: datetime
    cohort_bucket: CohortBucket
    cohort_rank: int
    final_shortlist: bool
    primary: bool
    eligible: bool
    score: float
    algorithm_version: str
    config_version: str
    feature_version: str
    provider_version: str
    fidelity: ReplayFidelity


@dataclass(frozen=True)
class LabeledSnapshot:
    snapshot: DecisionSnapshot
    outcome: ShadowOutcome
    ledger_affecting: bool


def label_shadow(snapshot: DecisionSnapshot, outcome: ShadowOutcome) -> LabeledSnapshot:
    return LabeledSnapshot(snapshot, outcome, ledger_affecting=False)


def _atomic_parquet(frame: pd.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(fd)
    temp = Path(name)
    try:
        frame.to_parquet(temp, index=False)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


class ResearchDatasetStore:
    """Immutable Parquet research snapshots and append-only shadow outcomes."""

    def __init__(self, root: str | Path) -> None:
        path = Path(root)
        self.root = path.parent / "research" if path.suffix == ".db" else path

    def _paths(self, snapshot_id: str, session: str) -> tuple[Path, Path]:
        try:
            month = datetime.fromisoformat(session).strftime("%Y/%m")
        except ValueError as exc:
            raise ValueError("research session must be ISO date") from exc
        directory = self.root / month
        return (
            directory / f"{snapshot_id}.candidates.parquet",
            directory / f"{snapshot_id}.outcomes.parquet",
        )

    def save_decision_rows(self, snapshot_id: str, rows: list[dict[str, object]]) -> None:
        symbols = {str(row.get("symbol", "")).upper() for row in rows}
        if len(rows) != 30 or len(symbols) != 30 or "" in symbols:
            raise ValueError("research snapshot must contain exactly 30 unique symbols")
        sessions = {str(row.get("session", "")) for row in rows}
        if len(sessions) != 1 or "" in sessions:
            raise ValueError("research snapshot rows must share one session")
        target, _ = self._paths(snapshot_id, sessions.pop())
        normalized = [
            {
                "snapshot_id": snapshot_id,
                "symbol": str(row["symbol"]).upper(),
                "payload": json.dumps(row, sort_keys=True, separators=(",", ":")),
            }
            for row in rows
        ]
        frame = pd.DataFrame(normalized).sort_values("symbol", kind="stable").reset_index(drop=True)
        if target.exists():
            existing = pd.read_parquet(target).sort_values("symbol", kind="stable").reset_index(drop=True)
            if not existing.equals(frame):
                raise ValueError("immutable research decision snapshot already exists with different data")
            return
        _atomic_parquet(frame, target)

    def record_outcome(
        self,
        snapshot_id: str,
        symbol: str,
        payload: dict[str, object],
        *,
        recorded_at: datetime,
        session: str,
    ) -> None:
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        _, target = self._paths(snapshot_id, session)
        row = {
            "snapshot_id": snapshot_id,
            "symbol": symbol.upper(),
            "recorded_at": recorded_at.isoformat(),
            "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
        }
        if target.exists():
            frame = pd.read_parquet(target)
            existing = frame.loc[frame["symbol"].eq(row["symbol"])]
            if not existing.empty:
                if existing.iloc[0]["payload"] != row["payload"]:
                    raise ValueError("immutable research outcome already exists with different data")
                return
            frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
        else:
            frame = pd.DataFrame([row])
        _atomic_parquet(frame.sort_values("symbol", kind="stable").reset_index(drop=True), target)

    def outcome_count(self, snapshot_id: str, *, session: str) -> int:
        _, target = self._paths(snapshot_id, session)
        if not target.exists():
            return 0
        frame = pd.read_parquet(target)
        return int(frame["snapshot_id"].eq(snapshot_id).sum())
