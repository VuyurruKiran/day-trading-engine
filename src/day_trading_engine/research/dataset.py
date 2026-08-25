from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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
