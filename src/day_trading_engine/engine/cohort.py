from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from .domain import CandidateInput, CohortBucket, CohortMember


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
    return hashlib.sha256(f"{session_key}:{symbol}".encode()).digest()


def build_research_cohort(
    ranked_valid: Sequence[CandidateInput],
    *,
    session_key: str,
    policy: CohortPolicy | None = None,
) -> tuple[CohortMember, ...]:
    policy = policy or CohortPolicy()
    unique: list[CandidateInput] = []
    seen: set[str] = set()
    for candidate in ranked_valid:
        symbol = candidate.symbol.upper()
        if symbol in seen:
            continue
        seen.add(symbol)
        unique.append(candidate)

    if len(unique) <= policy.target:
        members: list[CohortMember] = []
        for i, c in enumerate(unique):
            if i < policy.core:
                bucket = CohortBucket.CORE
            elif i < policy.core + policy.boundary:
                bucket = CohortBucket.BOUNDARY
            else:
                bucket = CohortBucket.DIVERSITY
            members.append(CohortMember(c, bucket, i + 1))
        return tuple(members)

    core = unique[: policy.core]
    boundary = unique[policy.core : policy.core + policy.boundary]
    remaining = unique[policy.core + policy.boundary :]
    diversity = sorted(remaining, key=lambda c: _stable_key(c.symbol.upper(), session_key))[
        : policy.diversity
    ]
    members = [
        *(CohortMember(c, CohortBucket.CORE, i + 1) for i, c in enumerate(core)),
        *(
            CohortMember(c, CohortBucket.BOUNDARY, policy.core + i + 1)
            for i, c in enumerate(boundary)
        ),
        *(CohortMember(c, CohortBucket.DIVERSITY, unique.index(c) + 1) for c in diversity),
    ]
    return tuple(members)
