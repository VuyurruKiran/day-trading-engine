from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChallengerResult:
    challenger_id: str
    primary_metric: float
    max_drawdown: float
    replay_deterministic: bool
    forward_confirmed: bool


@dataclass
class ChampionCycle:
    champion_id: str
    cycle_id: str
    promoted: bool = False
    forward_frozen: bool = False

    def consider(
        self,
        challenger: ChallengerResult,
        *,
        champion_metric: float,
        max_drawdown_limit: float,
    ) -> str:
        if self.promoted or self.forward_frozen:
            return "NO CHANGE"
        if not challenger.replay_deterministic or not challenger.forward_confirmed:
            return "NO CHANGE"
        if (
            challenger.primary_metric <= champion_metric
            or challenger.max_drawdown > max_drawdown_limit
        ):
            return "NO CHANGE"
        self.champion_id = challenger.challenger_id
        self.promoted = True
        self.forward_frozen = True
        return "PROMOTED"

    def complete_forward_cycle(self, cycle_id: str) -> None:
        self.cycle_id = cycle_id
        self.promoted = False
        self.forward_frozen = False


@dataclass(frozen=True)
class ExperimentRecord:
    challenger_id: str
    hypothesis: str
    result: str


@dataclass
class ExperimentLog:
    records: list[ExperimentRecord]

    def __init__(self) -> None:
        self.records = []

    def add(self, challenger_id: str, hypothesis: str, result: str) -> None:
        self.records.append(ExperimentRecord(challenger_id, hypothesis, result))
