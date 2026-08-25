from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from statistics import fmean


@dataclass(frozen=True)
class SessionResult:
    session: str
    candidate_rows: int
    triggered_shadow_setups: int
    paper_trades: int
    returns: tuple[float, ...]
    complete: bool = True


@dataclass(frozen=True)
class EvidenceReport:
    candidate_rows: int
    complete_sessions: int
    triggered_shadow_setups: int
    paper_trades: int
    expectancy: float
    eligible_for_promotion_review: bool


def build_evidence_report(
    results: list[SessionResult], *, min_complete_sessions: int = 15
) -> EvidenceReport:
    if min_complete_sessions < 1:
        raise ValueError("min_complete_sessions must be positive")
    complete = [row for row in results if row.complete]
    returns = [value for row in complete for value in row.returns]
    if any(not isfinite(value) for value in returns):
        raise ValueError("session returns must be finite")
    return EvidenceReport(
        candidate_rows=sum(row.candidate_rows for row in complete),
        complete_sessions=len({row.session for row in complete}),
        triggered_shadow_setups=sum(row.triggered_shadow_setups for row in complete),
        paper_trades=sum(row.paper_trades for row in complete),
        expectancy=fmean(returns) if returns else 0.0,
        eligible_for_promotion_review=(
            len({row.session for row in complete}) >= min_complete_sessions
        ),
    )


class HoldoutRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS consumed_holdouts "
                "(holdout_id TEXT PRIMARY KEY)"
            )

    def consume(self, holdout_id: str) -> None:
        holdout_id = holdout_id.strip()
        if not holdout_id:
            raise ValueError("holdout_id is required")
        try:
            with sqlite3.connect(self.path) as db:
                db.execute("INSERT INTO consumed_holdouts VALUES (?)", (holdout_id,))
        except sqlite3.IntegrityError as exc:
            raise ValueError("holdout has already influenced a decision") from exc


def max_drawdown(returns: list[float]) -> float:
    if any(not isfinite(value) or value <= -1 for value in returns):
        raise ValueError("returns must be finite and greater than -1")
    equity = peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    return drawdown


def hit_rate(returns: list[float]) -> float:
    if any(not isfinite(value) for value in returns):
        raise ValueError("returns must be finite")
    return sum(value > 0 for value in returns) / len(returns) if returns else 0.0
