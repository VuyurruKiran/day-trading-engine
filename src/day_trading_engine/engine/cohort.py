from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite


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


def build_research_cohort(
    candidates: list[ResearchCandidate] | tuple[ResearchCandidate, ...],
    *,
    session_key: str,
    target: int = 30,
    core_count: int = 20,
    boundary_count: int = 5,
) -> CohortResult:
    """Freeze the deterministic V1 20/core + 5/boundary + diversity cohort."""
    if target <= 0 or core_count < 0 or boundary_count < 0:
        raise ValueError("cohort counts must be non-negative and target must be positive")
    if core_count + boundary_count > target:
        raise ValueError("core + boundary cannot exceed target")
    if not session_key.strip():
        raise ValueError("session_key is required")

    unique: dict[str, ResearchCandidate] = {}
    for candidate in candidates:
        symbol = candidate.symbol.strip().upper()
        if not candidate.valid or not symbol:
            continue
        rank_score = float(candidate.rank_score)
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
    diversity = sorted(
        remaining,
        key=lambda item: sha256(f"{session_key}:{item.symbol}".encode()).digest(),
    )[:diversity_count]

    selected = [
        *(
            CohortMember(item.symbol, index + 1, "core", "top-ranked valid symbol")
            for index, item in enumerate(core)
        ),
        *(
            CohortMember(
                item.symbol,
                core_count + index + 1,
                "boundary",
                "valid symbol just below core cutoff",
            )
            for index, item in enumerate(boundary)
        ),
        *(
            CohortMember(
                item.symbol,
                core_count + boundary_count + index + 1,
                "diversity",
                "deterministic diversity sample",
            )
            for index, item in enumerate(diversity)
        ),
    ]
    return CohortResult(tuple(selected), max(0, target - len(selected)))
