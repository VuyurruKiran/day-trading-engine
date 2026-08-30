from __future__ import annotations

import json
import os
import sqlite3
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


def _effective_weights(row: dict[str, object]) -> dict[str, float]:
    context = row.get("context")
    context = context if isinstance(context, dict) else {}
    weights = {
        "technical": 0.50,
        "market": 0.20,
        "news": 0.20,
        "reddit": 0.05,
        "fundamentals": 0.05,
    }
    optional = (
        ("news_score", "news"),
        ("social_score", "reddit"),
        ("fundamental_score", "fundamentals"),
    )
    for score_name, weight_name in optional:
        if context.get(score_name) is None:
            weights["technical"] += weights[weight_name]
            weights[weight_name] = 0.0
    return weights


class ResearchDatasetStore:
    """Immutable Parquet research snapshots and append-only shadow outcomes."""

    def __init__(self, root: str | Path) -> None:
        path = Path(root)
        self.state_db = path.parent / "decision_state.db" if path.suffix == ".db" else None
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

    def _report_metadata(self, snapshot_id: str) -> dict[str, object]:
        if self.state_db is None or not self.state_db.exists():
            return {}
        with sqlite3.connect(self.state_db) as db:
            row = db.execute(
                "SELECT created_at, payload FROM reports WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
        if row is None:
            return {}
        payload = json.loads(row[1])
        return {
            "decision_at": row[0],
            "session": payload.get("session"),
            "algorithm_version": payload.get("algorithm"),
            "software_version": payload.get("software_version"),
            "feature_version": payload.get("feature_version"),
            "ranking_version": payload.get("ranking_version"),
            "config_version": "3.1",
            "available_cash_usd": payload.get("available_cash_usd"),
            "benchmark_symbols": payload.get("benchmark_symbols"),
        }

    def save_decision_rows(self, snapshot_id: str, rows: list[dict[str, object]]) -> None:
        symbols = {str(row.get("symbol", "")).upper() for row in rows}
        if len(rows) != 30 or len(symbols) != 30 or "" in symbols:
            raise ValueError("research snapshot must contain exactly 30 unique symbols")
        metadata = self._report_metadata(snapshot_id)
        session = str(metadata.get("session") or rows[0].get("session") or snapshot_id[:10])
        target, _ = self._paths(snapshot_id, session)
        normalized = []
        for row in rows:
            payload = {**metadata, **row}
            payload["session"] = session
            payload["decision_snapshot_id"] = snapshot_id
            payload["final_score"] = row.get("rank_score")
            payload["effective_weights"] = _effective_weights(row)
            normalized.append(
                {
                    "snapshot_id": snapshot_id,
                    "symbol": str(row["symbol"]).upper(),
                    "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                }
            )
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
        session: str | None = None,
    ) -> None:
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        _, target = self._paths(snapshot_id, session or snapshot_id[:10])
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

    def outcome_count(self, snapshot_id: str, *, session: str | None = None) -> int:
        _, target = self._paths(snapshot_id, session or snapshot_id[:10])
        if not target.exists():
            return 0
        frame = pd.read_parquet(target)
        return int(frame["snapshot_id"].eq(snapshot_id).sum())
