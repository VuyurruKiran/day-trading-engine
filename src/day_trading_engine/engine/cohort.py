from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite

from .domain import CandidateInput, CohortBucket
from .domain import CohortMember as DomainCohortMember


@dataclass(frozen=True)
class ResearchCandidate:
    symbol: str
    rank_score: float
    valid: bool = True


@dataclass(frozen=True)
class CohortMember:
    symbol: str
    rank: int
    bucket: str
    reason: str


@dataclass(frozen=True)
class CohortResult:
    members: tuple[CohortMember, ...]
    shortfall: int


@dataclass(frozen=True)
class CohortPolicy:
    target: int = 30
    core: int = 20
    boundary: int = 5
    diversity: int = 5

    def __post_init__(self) -> None:
        if min(self.target, self.core, self.boundary, self.diversity) < 0:
            raise ValueError("cohort counts cannot be negative")
        if self.core + self.boundary + self.diversity != self.target:
            raise ValueError("cohort buckets must sum to target")


def _stable_key(symbol: str, session_key: str) -> bytes:
    return sha256(f"{session_key}:{symbol}".encode()).digest()


def build_research_cohort(
    candidates: Sequence[ResearchCandidate | CandidateInput],
    *,
    session_key: str,
    target: int = 30,
    core_count: int = 20,
    boundary_count: int = 5,
    policy: CohortPolicy | None = None,
) -> CohortResult | tuple[DomainCohortMember, ...]:
    """Freeze the v2.2 research cohort while preserving the M4 compatibility API."""
    if policy is not None or (candidates and isinstance(candidates[0], CandidateInput)):
        return _build_candidate_members(candidates, session_key=session_key, policy=policy)
    if target <= 0 or core_count < 0 or boundary_count < 0:
        raise ValueError("cohort counts must be non-negative and target must be positive")
    if core_count + boundary_count > target:
        raise ValueError("core + boundary cannot exceed target")
    if not session_key.strip():
        raise ValueError("session_key is required")

    unique: dict[str, ResearchCandidate] = {}
    for item in candidates:
        if not isinstance(item, ResearchCandidate):
            raise TypeError("research cohort candidates must use one candidate model")
        symbol = item.symbol.strip().upper()
        if not item.valid or not symbol:
            continue
        rank_score = float(item.rank_score)
        if not isfinite(rank_score):
            continue
        current = unique.get(symbol)
        normalized = ResearchCandidate(symbol=symbol, rank_score=rank_score)
        if current is None or normalized.rank_score > current.rank_score:
            unique[symbol] = normalized

    ranked = sorted(unique.values(), key=lambda item: (-item.rank_score, item.symbol))
    core = ranked[:core_count]
    boundary = ranked[core_count : core_count + boundary_count]
    remaining = ranked[core_count + boundary_count :]
    diversity_count = max(0, target - len(core) - len(boundary))
    diversity = sorted(remaining, key=lambda item: _stable_key(item.symbol, session_key))[
        :diversity_count
    ]
    selected = [
        *(CohortMember(item.symbol, i + 1, "core", "top-ranked valid symbol") for i, item in enumerate(core)),
        *(
            CohortMember(item.symbol, core_count + i + 1, "boundary", "valid symbol just below core cutoff")
            for i, item in enumerate(boundary)
        ),
        *(
            CohortMember(
                item.symbol,
                core_count + boundary_count + i + 1,
                "diversity",
                "deterministic diversity sample",
            )
            for i, item in enumerate(diversity)
        ),
    ]
    return CohortResult(tuple(selected), max(0, target - len(selected)))


def _build_candidate_members(
    candidates: Sequence[ResearchCandidate | CandidateInput],
    *,
    session_key: str,
    policy: CohortPolicy | None,
) -> tuple[DomainCohortMember, ...]:
    selected_policy = policy or CohortPolicy()
    rows = [item for item in candidates if isinstance(item, CandidateInput)]
    if len(rows) != len(candidates):
        raise TypeError("research cohort candidates must use one candidate model")
    unique: list[CandidateInput] = []
    seen: set[str] = set()
    for candidate in rows:
        if candidate.symbol in seen:
            continue
        seen.add(candidate.symbol)
        unique.append(candidate)
    core = unique[: selected_policy.core]
    boundary = unique[selected_policy.core : selected_policy.core + selected_policy.boundary]
    remaining = unique[selected_policy.core + selected_policy.boundary :]
    diversity = sorted(remaining, key=lambda item: _stable_key(item.symbol, session_key))[
        : selected_policy.diversity
    ]
    members = [
        *(DomainCohortMember(item, CohortBucket.CORE, i + 1) for i, item in enumerate(core)),
        *(
            DomainCohortMember(item, CohortBucket.BOUNDARY, selected_policy.core + i + 1)
            for i, item in enumerate(boundary)
        ),
        *(
            DomainCohortMember(item, CohortBucket.DIVERSITY, unique.index(item) + 1)
            for item in diversity
        ),
    ]
    return tuple(members[: selected_policy.target])
