from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path

import pandas as pd

from day_trading_engine.engine.domain import CohortBucket
from day_trading_engine.paper.replay import ReplayFidelity, ShadowOutcome

_CONTROL_COUNT = 3


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


def _score(row: dict[str, object]) -> float:
    value = row.get("rank_score")
    try:
        score = float(value)
    except (TypeError, ValueError):
        return float("-inf")
    return score if isfinite(score) else float("-inf")


class ResearchDatasetStore:
    """Immutable research snapshots plus DB-backed refinement telemetry."""

    def __init__(self, root: str | Path) -> None:
        path = Path(root)
        self.state_db = path.parent / "decision_state.db" if path.suffix == ".db" else None
        self.tracking_db = path.parent / "trading.db" if path.suffix == ".db" else None
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
                "SELECT created_at, payload FROM reports WHERE snapshot_id = ?",
                (snapshot_id,),
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

    def save_selection_explanations(
        self,
        snapshot_id: str,
        rows: list[dict[str, object]],
    ) -> None:
        """Persist finalists and three near-miss controls with exact decision evidence."""
        if self.tracking_db is None:
            return
        metadata = self._report_metadata(snapshot_id)
        ranked = sorted(
            (row for row in rows if _score(row) != float("-inf")),
            key=lambda row: (-_score(row), str(row.get("symbol", ""))),
        )
        rank_by_symbol = {
            str(row.get("symbol", "")).upper(): rank
            for rank, row in enumerate(ranked, start=1)
        }
        finalists = [row for row in ranked if row.get("finalist") is True]
        if not finalists:
            return
        controls = [row for row in ranked if row.get("finalist") is not True][:_CONTROL_COUNT]
        tracked = [*finalists, *controls]
        session = str(metadata.get("session") or snapshot_id[:10])
        selected_at = str(metadata.get("decision_at") or "")

        self.tracking_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.tracking_db) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_selections (
                    snapshot_id TEXT NOT NULL,
                    session TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('PRIMARY', 'FINALIST', 'CONTROL')),
                    final_rank INTEGER NOT NULL,
                    final_score REAL NOT NULL,
                    decision_price REAL NOT NULL,
                    entry REAL,
                    stop REAL,
                    target REAL,
                    selected_at TEXT NOT NULL,
                    explanation_json TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_research_selections_session_symbol
                    ON research_selections(session, symbol);
                CREATE TABLE IF NOT EXISTS research_monitoring (
                    snapshot_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    role TEXT NOT NULL,
                    bucket_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    price REAL,
                    bid REAL,
                    ask REAL,
                    volume INTEGER,
                    return_pct REAL,
                    mfe_pct REAL,
                    mae_pct REAL,
                    target_hit INTEGER NOT NULL,
                    stop_hit INTEGER NOT NULL,
                    quote_eligible INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, symbol, bucket_at)
                );
                CREATE INDEX IF NOT EXISTS idx_research_monitoring_snapshot_symbol
                    ON research_monitoring(snapshot_id, symbol, observed_at);
                """
            )
            for row in tracked:
                symbol = str(row.get("symbol", "")).upper()
                context = row.get("context")
                context = context if isinstance(context, dict) else {}
                features = row.get("features")
                features = features if isinstance(features, dict) else {}
                plan = row.get("plan")
                plan = plan if isinstance(plan, dict) else {}
                role = (
                    "PRIMARY"
                    if row.get("primary") is True
                    else "FINALIST"
                    if row.get("finalist") is True
                    else "CONTROL"
                )
                explanation = {
                    "role": role,
                    "final_rank": rank_by_symbol[symbol],
                    "final_score": _score(row),
                    "technical_score": row.get("technical_score"),
                    "market_score": context.get("market_score", features.get("market_score")),
                    "news_score": context.get("news_score"),
                    "reddit_score": context.get("social_score"),
                    "fundamental_score": context.get("fundamental_score"),
                    "effective_weights": _effective_weights(row),
                    "reasons": row.get("reasons", []),
                    "evidence_counts": context.get("evidence_counts", {}),
                    "cohort_rank": row.get("cohort_rank"),
                    "cohort_reason": row.get("cohort_reason"),
                    "plan": plan or None,
                    "feature_version": metadata.get("feature_version"),
                    "ranking_version": metadata.get("ranking_version"),
                    "algorithm_version": metadata.get("algorithm_version"),
                    "config_version": metadata.get("config_version", "3.1"),
                }
                decision_price = float(features.get("price", plan.get("entry", 0.0)))
                if not isfinite(decision_price) or decision_price <= 0:
                    continue
                db.execute(
                    """
                    INSERT OR IGNORE INTO research_selections(
                        snapshot_id, session, symbol, role, final_rank, final_score,
                        decision_price, entry, stop, target, selected_at, explanation_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        session,
                        symbol,
                        role,
                        rank_by_symbol[symbol],
                        _score(row),
                        decision_price,
                        plan.get("entry"),
                        plan.get("stop"),
                        plan.get("target"),
                        selected_at,
                        json.dumps(explanation, sort_keys=True, separators=(",", ":")),
                    ),
                )

    def save_decision_rows(self, snapshot_id: str, rows: list[dict[str, object]]) -> None:
        symbols = {str(row.get("symbol", "")).upper() for row in rows}
        if len(rows) != 30 or len(symbols) != 30 or "" in symbols:
            raise ValueError("research snapshot must contain exactly 30 unique symbols")
        metadata = self._report_metadata(snapshot_id)
        session = str(
            metadata.get("session") or rows[0].get("session") or snapshot_id[:10]
        )
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
                    "payload": json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )
        self.save_selection_explanations(snapshot_id, rows)
        frame = pd.DataFrame(normalized)
        frame = frame.sort_values("symbol", kind="stable").reset_index(drop=True)
        if target.exists():
            existing = pd.read_parquet(target)
            existing = existing.sort_values("symbol", kind="stable").reset_index(drop=True)
            if not existing.equals(frame):
                raise ValueError(
                    "immutable research decision snapshot already exists with different data"
                )
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
                    raise ValueError(
                        "immutable research outcome already exists with different data"
                    )
                return
            frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
        else:
            frame = pd.DataFrame([row])
        frame = frame.sort_values("symbol", kind="stable").reset_index(drop=True)
        _atomic_parquet(frame, target)

    def outcome_count(self, snapshot_id: str, *, session: str | None = None) -> int:
        _, target = self._paths(snapshot_id, session or snapshot_id[:10])
        if not target.exists():
            return 0
        frame = pd.read_parquet(target)
        return int(frame["snapshot_id"].eq(snapshot_id).sum())
