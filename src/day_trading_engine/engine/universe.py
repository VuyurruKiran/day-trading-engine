from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True, slots=True)
class UniverseCandidate:
    symbol: str
    security_id: str
    exchange: str
    asset_type: str
    sector: str
    price: float
    median_dollar_volume: float
    spread_pct: float
    volatility: float
    coverage_ratio: float
    provider_resolvable: bool = True
    active: bool = True
    corporate_action_ok: bool = True
    is_ipo: bool = False
    listing_sessions: int = 9999

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol or not self.security_id.strip() or not self.exchange.strip():
            raise ValueError("universe identity fields are required")
        values = (
            self.price,
            self.median_dollar_volume,
            self.spread_pct,
            self.volatility,
            self.coverage_ratio,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("universe measurements must be finite")
        if self.price <= 0 or min(self.median_dollar_volume, self.spread_pct, self.volatility) < 0:
            raise ValueError("universe price/volume/spread/volatility values are invalid")
        if not 0 <= self.coverage_ratio <= 1 or self.listing_sessions < 0:
            raise ValueError("universe coverage/listing-session values are invalid")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "sector", self.sector.strip() or "UNKNOWN")


@dataclass(frozen=True, slots=True)
class UniverseSelectionRow:
    symbol: str
    security_id: str
    exchange: str
    asset_type: str
    sector: str
    score: float | None
    included: bool
    reason: str


@dataclass(frozen=True, slots=True)
class UniverseProvenance:
    catalog_provider: str
    metrics_provider: str
    metrics_feed: str
    metrics_start: str
    metrics_end: str
    identity_provider: str
    quote_provider: str
    quote_received_at: str


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    universe_id: str
    effective_from: str
    selector_version: str
    config_version: str
    target: int
    members: tuple[UniverseSelectionRow, ...]
    exclusions: tuple[UniverseSelectionRow, ...]
    created_at: str
    checksum: str
    provenance: UniverseProvenance | None = None

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(row.symbol for row in self.members)


def _bounded(value: float) -> float:
    return min(1.0, max(0.0, value))


def universe_score(candidate: UniverseCandidate, *, max_spread_pct: float) -> float:
    """Stable monthly capacity score; this is intentionally not the trading score."""
    liquidity = _bounded(math.log10(max(1.0, candidate.median_dollar_volume)) / 9.0)
    spread = 1.0 - _bounded(candidate.spread_pct / max_spread_pct)
    opportunity = _bounded(candidate.volatility / 0.05)
    return round(
        0.45 * liquidity + 0.25 * spread + 0.15 * opportunity + 0.15 * candidate.coverage_ratio,
        10,
    )


def _eligibility_reason(
    candidate: UniverseCandidate,
    *,
    cash_usd: float,
    max_spread_pct: float,
    min_coverage_ratio: float,
    ipo_seasoning_sessions: int,
) -> str | None:
    if not candidate.active:
        return "inactive security"
    if not candidate.provider_resolvable:
        return "live provider cannot resolve symbol"
    if candidate.asset_type not in {"common_stock", "approved_etf"}:
        return "unsupported asset type"
    if not candidate.corporate_action_ok:
        return "unresolved corporate action"
    if candidate.price > cash_usd:
        return "price exceeds cash-only universe limit"
    if candidate.median_dollar_volume <= 0:
        return "insufficient liquidity history"
    if candidate.spread_pct > max_spread_pct:
        return "spread quality below universe limit"
    if candidate.coverage_ratio < min_coverage_ratio:
        return "historical coverage below universe limit"
    if candidate.is_ipo and candidate.listing_sessions < ipo_seasoning_sessions:
        return "IPO seasoning period incomplete"
    return None


def _snapshot_basis(
    *,
    effective_from: str,
    selector_version: str,
    config_version: str,
    target: int,
    members: tuple[UniverseSelectionRow, ...] | list[UniverseSelectionRow],
    exclusions: tuple[UniverseSelectionRow, ...] | list[UniverseSelectionRow],
    provenance: UniverseProvenance | None,
) -> dict[str, object]:
    basis: dict[str, object] = {
        "effective_from": effective_from,
        "selector_version": selector_version,
        "config_version": config_version,
        "target": target,
        "members": [asdict(row) for row in members],
        "exclusions": [asdict(row) for row in exclusions],
    }
    if provenance is not None:
        basis["provenance"] = asdict(provenance)
    return basis


def select_research_universe(
    candidates: list[UniverseCandidate] | tuple[UniverseCandidate, ...],
    *,
    effective_from: date,
    target: int,
    cash_usd: float,
    max_spread_pct: float,
    min_coverage_ratio: float,
    max_sector_fraction: float,
    ipo_seasoning_sessions: int,
    selector_version: str,
    config_version: str,
    provenance: UniverseProvenance | None = None,
) -> UniverseSnapshot:
    if target < 1 or not math.isfinite(cash_usd) or cash_usd <= 0:
        raise ValueError("universe target and cash must be positive")
    if not 0 < max_sector_fraction <= 1:
        raise ValueError("max_sector_fraction must be in (0,1]")

    unique: dict[str, UniverseCandidate] = {}
    for candidate in candidates:
        current = unique.get(candidate.symbol)
        if current is not None and current.security_id != candidate.security_id:
            raise ValueError(f"conflicting security identity for {candidate.symbol}")
        unique[candidate.symbol] = candidate

    eligible: list[tuple[UniverseCandidate, float]] = []
    exclusions: list[UniverseSelectionRow] = []
    for candidate in unique.values():
        reason = _eligibility_reason(
            candidate,
            cash_usd=cash_usd,
            max_spread_pct=max_spread_pct,
            min_coverage_ratio=min_coverage_ratio,
            ipo_seasoning_sessions=ipo_seasoning_sessions,
        )
        if reason:
            exclusions.append(_selection_row(candidate, None, False, reason))
            continue
        eligible.append((candidate, universe_score(candidate, max_spread_pct=max_spread_pct)))

    eligible.sort(key=lambda item: (-item[1], item[0].symbol, item[0].security_id))
    sector_limit = max(1, math.ceil(target * max_sector_fraction))
    sector_counts: dict[str, int] = {}
    members: list[UniverseSelectionRow] = []
    for candidate, score in eligible:
        if len(members) >= target:
            exclusions.append(
                _selection_row(candidate, score, False, "below active-universe cutoff")
            )
            continue
        if candidate.sector != "UNKNOWN":
            count = sector_counts.get(candidate.sector, 0)
            if count >= sector_limit:
                exclusions.append(
                    _selection_row(candidate, score, False, "sector concentration limit")
                )
                continue
            sector_counts[candidate.sector] = count + 1
        members.append(_selection_row(candidate, score, True, "selected by monthly universe score"))

    sorted_exclusions = sorted(exclusions, key=lambda row: row.symbol)
    basis = _snapshot_basis(
        effective_from=effective_from.isoformat(),
        selector_version=selector_version,
        config_version=config_version,
        target=target,
        members=members,
        exclusions=sorted_exclusions,
        provenance=provenance,
    )
    digest = sha256(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return UniverseSnapshot(
        universe_id=f"US-{effective_from:%Y-%m}-{digest[:12]}",
        effective_from=effective_from.isoformat(),
        selector_version=selector_version,
        config_version=config_version,
        target=target,
        members=tuple(members),
        exclusions=tuple(sorted_exclusions),
        created_at=datetime.now(UTC).isoformat(),
        checksum=digest,
        provenance=provenance,
    )


def _selection_row(
    candidate: UniverseCandidate, score: float | None, included: bool, reason: str
) -> UniverseSelectionRow:
    return UniverseSelectionRow(
        candidate.symbol,
        candidate.security_id,
        candidate.exchange,
        candidate.asset_type,
        candidate.sector,
        score,
        included,
        reason,
    )


def _quarantine_invalid_snapshot_peers(root: Path, target: Path, effective_from: date) -> None:
    target_month = (effective_from.year, effective_from.month)
    for path in root.glob("US-*.json"):
        if path == target:
            continue
        peer_effective: date | None = None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            snapshot = _snapshot_from_payload(payload)
            peer_effective = date.fromisoformat(snapshot.effective_from)
            created = datetime.fromisoformat(snapshot.created_at)
            if created.tzinfo is None or created.utcoffset() is None:
                raise ValueError("universe snapshot created_at must be timezone-aware")
            if (peer_effective.year, peer_effective.month) != target_month:
                continue
        except (OSError, KeyError, OverflowError, TypeError, ValueError):
            peer_month = _snapshot_file_month(path)
            if peer_month is None and peer_effective is not None:
                peer_month = (peer_effective.year, peer_effective.month)
            if peer_month == target_month:
                os.replace(path, path.with_name(f"{path.name}.invalid"))


def write_universe_snapshot(root: Path, snapshot: UniverseSnapshot) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{snapshot.universe_id}.json"
    payload = asdict(snapshot)
    payload["survivorship_risk"] = (
        "Historical replay must use the universe version effective on the replay date."
    )
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        _quarantine_invalid_snapshot_peers(
            root, target, date.fromisoformat(snapshot.effective_from)
        )
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return target


def _snapshot_from_payload(payload: dict[str, object]) -> UniverseSnapshot:
    members = tuple(UniverseSelectionRow(**row) for row in payload["members"])
    exclusions = tuple(UniverseSelectionRow(**row) for row in payload["exclusions"])
    provenance_payload = payload.get("provenance")
    provenance = (
        UniverseProvenance(**provenance_payload)
        if isinstance(provenance_payload, dict)
        else None
    )
    snapshot = UniverseSnapshot(
        universe_id=str(payload["universe_id"]),
        effective_from=str(payload["effective_from"]),
        selector_version=str(payload["selector_version"]),
        config_version=str(payload["config_version"]),
        target=int(payload["target"]),
        members=members,
        exclusions=exclusions,
        created_at=str(payload["created_at"]),
        checksum=str(payload["checksum"]),
        provenance=provenance,
    )
    basis = _snapshot_basis(
        effective_from=snapshot.effective_from,
        selector_version=snapshot.selector_version,
        config_version=snapshot.config_version,
        target=snapshot.target,
        members=snapshot.members,
        exclusions=snapshot.exclusions,
        provenance=snapshot.provenance,
    )
    digest = sha256(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != snapshot.checksum:
        raise ValueError("universe snapshot checksum mismatch")
    return snapshot


def _snapshot_file_month(path: Path) -> tuple[int, int] | None:
    parts = path.stem.split("-")
    if len(parts) < 3 or parts[0] != "US":
        return None
    try:
        year, month = int(parts[1]), int(parts[2])
        date(year, month, 1)
    except ValueError:
        return None
    return year, month


def load_universe_snapshot(
    root: Path, *, as_of: date, ignore_invalid: bool = False
) -> UniverseSnapshot | None:
    candidates: list[tuple[date, datetime, Path, UniverseSnapshot]] = []
    invalid_months: set[tuple[int, int]] = set()
    as_of_month = (as_of.year, as_of.month)
    if not root.exists():
        return None
    for path in root.glob("US-*.json"):
        effective: date | None = None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            snapshot = _snapshot_from_payload(payload)
            effective = date.fromisoformat(snapshot.effective_from)
            created = datetime.fromisoformat(snapshot.created_at)
            if created.tzinfo is None or created.utcoffset() is None:
                raise ValueError("universe snapshot created_at must be timezone-aware")
            if effective > as_of:
                continue
        except (OSError, KeyError, OverflowError, TypeError, ValueError):
            if not ignore_invalid:
                raise
            invalid_month = _snapshot_file_month(path)
            if invalid_month is None and effective is not None:
                invalid_month = (effective.year, effective.month)
            if invalid_month is not None and invalid_month <= as_of_month:
                invalid_months.add(invalid_month)
            continue
        candidates.append((effective, created, path, snapshot))
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: (item[0], item[1], item[2].name))
    latest_month = (latest[0].year, latest[0].month)
    if ignore_invalid and any(month >= latest_month for month in invalid_months):
        return None
    return latest[3]
