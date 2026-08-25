from __future__ import annotations

from dataclasses import dataclass
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
    complete = [row for row in results if row.complete]
    returns = [value for row in complete for value in row.returns]
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


@dataclass
class HoldoutRegistry:
    consumed: set[str]

    def __init__(self) -> None:
        self.consumed = set()

    def consume(self, holdout_id: str) -> None:
        if holdout_id in self.consumed:
            raise ValueError("holdout has already influenced a decision")
        self.consumed.add(holdout_id)


def max_drawdown(returns: list[float]) -> float:
    equity = peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    return drawdown


def hit_rate(returns: list[float]) -> float:
    return sum(value > 0 for value in returns) / len(returns) if returns else 0.0
