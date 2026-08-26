from __future__ import annotations

import argparse
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, time, timedelta
from math import isfinite
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from day_trading_engine.core.config import AppConfig, load_config
from day_trading_engine.core.paths import project_root
from day_trading_engine.engine.cohort import ResearchCandidate, build_research_cohort
from day_trading_engine.engine.domain import CandidateDecision, CandidateInput
from day_trading_engine.engine.ranking import rank_all
from day_trading_engine.engine.strategy import (
    CandidateSnapshot,
    StrategyPolicy,
    TradePlan,
    evaluate_baseline,
)
from day_trading_engine.features.market import FEATURE_VERSION, build_market_features
from day_trading_engine.market_data.store import MarketDataStore, StoredQuote, parse_timestamp
from day_trading_engine.ui.state import ReportStore, SavedReport

_MAX_QUOTE_AGE = timedelta(minutes=5)
_EASTERN = ZoneInfo("America/New_York")
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
_OPENING_RANGE = timedelta(minutes=5)
_OPENING_START_TOLERANCE = timedelta(minutes=1)
_INSUFFICIENT_FEATURES = "insufficient intraday samples to build complete features"
_OPENING_COVERAGE_MISSING = "regular-session opening-range coverage is incomplete"


def _regular_session_frame(session: tuple[StoredQuote, ...]) -> pd.DataFrame:
    """Return only regular-session quote samples in chronological order."""
    frame = pd.DataFrame([asdict(record) for record in session])
    if frame.empty:
        return frame
    received = pd.to_datetime(frame["received_at"], utc=True, errors="raise")
    eastern = received.dt.tz_convert(_EASTERN)
    regular = (eastern.dt.time >= _REGULAR_OPEN) & (eastern.dt.time < _REGULAR_CLOSE)
    return frame.loc[regular].reset_index(drop=True)


def _has_opening_coverage(frame: pd.DataFrame) -> bool:
    """Require real samples spanning the first five minutes after the regular open."""
    if frame.empty:
        return False
    received = pd.to_datetime(frame["received_at"], utc=True, errors="raise").sort_values()
    first = received.iloc[0].to_pydatetime().astimezone(_EASTERN)
    open_at = first.replace(hour=9, minute=30, second=0, microsecond=0)
    if not (open_at <= first <= open_at + _OPENING_START_TOLERANCE):
        return False
    opening_end = open_at + _OPENING_RANGE
    opening = received[
        received.dt.tz_convert(_EASTERN) < pd.Timestamp(opening_end)
    ]
    last = received.iloc[-1].to_pydatetime().astimezone(_EASTERN)
    return len(opening) >= 5 and last >= opening_end


def _build_candidate(
    store: MarketDataStore,
    latest: StoredQuote,
    *,
    as_of: datetime,
) -> tuple[CandidateInput | None, str | None, dict[str, object]]:
    """Build one decision input and its immutable market-feature evidence."""
    session = store.session(latest.symbol, latest.received_at[:10])
    frame = _regular_session_frame(session)
    if frame.empty:
        return None, "no trade-eligible market samples for decision session", {}
    if not _has_opening_coverage(frame):
        return None, _OPENING_COVERAGE_MISSING, {}

    features = build_market_features(frame, as_of=as_of)
    if features.empty:
        return None, "no trade-eligible market samples for decision session", {}

    row = features.iloc[-1]
    required = (
        row["last_trade_price"],
        row["volume"],
        row["rvol"],
        row["vwap"],
        row["opening_range_high"],
        row["opening_range_low"],
        row["volatility"],
    )
    if any(pd.isna(value) or not isfinite(float(value)) for value in required):
        return None, _INSUFFICIENT_FEATURES, {}

    evidence: dict[str, object] = {
        "received_at": str(row["received_at"]),
        "price": float(row["last_trade_price"]),
        "bid": float(row["bid_price"]),
        "ask": float(row["ask_price"]),
        "volume": int(row["volume"]),
        "rvol": float(row["rvol"]),
        "vwap": float(row["vwap"]),
        "opening_range_high": float(row["opening_range_high"]),
        "opening_range_low": float(row["opening_range_low"]),
        "volatility": float(row["volatility"]),
        "spread_pct": float(row["spread_pct"]),
    }
    return (
        CandidateInput(
            symbol=latest.symbol,
            as_of=parse_timestamp(latest.received_at),
            price=float(evidence["price"]),
            bid=float(evidence["bid"]),
            ask=float(evidence["ask"]),
            volume=float(evidence["volume"]),
            rvol=float(evidence["rvol"]),
            vwap=float(evidence["vwap"]),
            opening_range_high=float(evidence["opening_range_high"]),
            opening_range_low=float(evidence["opening_range_low"]),
            volatility=float(evidence["volatility"]),
            delayed=latest.delay_seconds != 0,
            halted=latest.is_halted,
            provider_ok=latest.is_trade_eligible,
        ),
        None,
        evidence,
    )


def _strategy_policy(config: AppConfig) -> StrategyPolicy:
    """Map the locked V1 configuration to the implemented baseline policy."""
    return StrategyPolicy(
        max_spread_pct=config.risk.max_spread_pct,
        max_volatility=config.risk.max_volatility,
        min_rvol=config.risk.min_rvol,
        min_volume=config.risk.min_volume,
        entry_buffer_pct=config.strategy.entry_buffer_pct,
        stop_buffer_pct=config.strategy.stop_buffer_pct,
        reward_to_risk=config.strategy.reward_to_risk,
    )


def _snapshot(candidate: CandidateInput) -> CandidateSnapshot:
    """Convert the shared candidate input to the baseline strategy snapshot."""
    return CandidateSnapshot(
        symbol=candidate.symbol,
        price=candidate.price,
        bid=candidate.bid,
        ask=candidate.ask,
        volume=int(candidate.volume),
        rvol=candidate.rvol,
        volatility=candidate.volatility,
        vwap=candidate.vwap,
        opening_range_high=candidate.opening_range_high,
        fresh=candidate.provider_ok and not candidate.stale,
        delayed=candidate.delayed,
        halted=candidate.halted,
    )


def _plan_payload(plan: TradePlan) -> dict[str, object]:
    """Serialize a baseline trade plan for the immutable decision snapshot."""
    return {
        "symbol": plan.symbol,
        "status": plan.status,
        "score": plan.score,
        "entry": plan.entry,
        "stop": plan.stop,
        "target": plan.target,
        "quantity": plan.quantity,
        "expiry": plan.expiry,
    }


def _select_current_universe(
    latest: tuple[StoredQuote, ...],
    *,
    limit: int,
) -> tuple[StoredQuote, ...]:
    """Select at most the locked V1 universe size from the freshest symbols."""
    return tuple(
        sorted(
            latest,
            key=lambda record: (parse_timestamp(record.received_at), record.symbol),
            reverse=True,
        )[:limit]
    )


def _regular_session_timestamp(value: datetime) -> bool:
    """Return whether a timestamp belongs to a regular US-equity session."""
    eastern = value.astimezone(_EASTERN)
    return eastern.weekday() < 5 and _REGULAR_OPEN <= eastern.time() < _REGULAR_CLOSE


def _validate_decision_time(as_of: datetime, created: datetime) -> None:
    """Fail closed when the actual decision timestamp or newest quote is invalid."""
    if not _regular_session_timestamp(created):
        raise RuntimeError("decision run is outside the regular trading session")
    if not _regular_session_timestamp(as_of):
        raise RuntimeError("latest market quotes are outside the regular trading session")
    if created.astimezone(_EASTERN).date() != as_of.astimezone(_EASTERN).date():
        raise RuntimeError("latest market quotes are from a different decision session")
    if created < as_of:
        raise RuntimeError("decision timestamp precedes latest market quote")
    if created - as_of > _MAX_QUOTE_AGE:
        raise RuntimeError("latest market quotes are stale; run collect.ps1 before decision run")


def _validate_universe_freshness(current: tuple[StoredQuote, ...], created: datetime) -> None:
    """Require every locked-universe quote to be current for this decision."""
    for record in current:
        received = parse_timestamp(record.received_at)
        if not _regular_session_timestamp(received):
            raise RuntimeError(f"{record.symbol} quote is outside the regular trading session")
        if received.astimezone(_EASTERN).date() != created.astimezone(_EASTERN).date():
            raise RuntimeError(f"{record.symbol} quote is from a different decision session")
        age = created - received
        if age < timedelta(0):
            raise RuntimeError(f"{record.symbol} quote is future-dated")
        if age > _MAX_QUOTE_AGE:
            raise RuntimeError(
                f"{record.symbol} quote is stale; run collect.ps1 before decision run"
            )


def run_decision(
    *,
    config: AppConfig,
    market_store: MarketDataStore,
    report_store: ReportStore,
    created_at: datetime | None = None,
) -> SavedReport:
    """Build, evaluate, and persist one production V1 decision snapshot."""
    latest = market_store.latest_all()
    if not latest:
        raise RuntimeError("no stored market quotes; run collect.ps1 first")

    created = created_at or datetime.now(UTC)
    if created.tzinfo is None or created.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")

    target = config.research.daily_candidate_count
    current = _select_current_universe(latest, limit=target)
    if len(current) < target:
        raise RuntimeError(f"decision universe incomplete: need {target} current symbols")

    as_of = max(parse_timestamp(record.received_at) for record in current)
    _validate_decision_time(as_of, created)
    _validate_universe_freshness(current, created)

    session_key = as_of.astimezone(_EASTERN).date().isoformat()
    prepared: dict[str, tuple[CandidateInput | None, str | None, dict[str, object]]] = {}
    for record in current:
        if not record.is_trade_eligible:
            prepared[record.symbol] = (None, record.invalid_reason or "invalid market quote", {})
            continue
        prepared[record.symbol] = _build_candidate(market_store, record, as_of=as_of)

    discovery = [
        ResearchCandidate(
            record.symbol,
            float(record.volume or 0),
            valid=prepared[record.symbol][0] is not None,
        )
        for record in current
    ]
    cohort = build_research_cohort(
        discovery,
        session_key=session_key,
        target=target,
        core_count=config.research.core_candidate_count,
        boundary_count=config.research.boundary_candidate_count,
    )

    active_position = report_store.has_open_execution()
    cohort_inputs: list[CandidateInput] = []
    evidence: dict[str, dict[str, object]] = {}
    for member in cohort.members:
        candidate, _, features = prepared[member.symbol]
        if candidate is None:
            raise RuntimeError("cohort contains a candidate without complete features")
        cohort_inputs.append(candidate)
        evidence[member.symbol] = {
            "symbol": member.symbol,
            "cohort_rank": member.rank,
            "cohort_bucket": member.bucket,
            "cohort_reason": member.reason,
            "features": features,
        }

    rejected_inputs = [
        {"symbol": symbol, "reason": reason}
        for symbol, (candidate, reason, _) in prepared.items()
        if candidate is None
    ]
    data_not_ready = len(cohort.members) < target

    finalist_payload: list[dict[str, object]] = []
    primary: dict[str, object] | None = None
    baseline_no_trade_reason: str | None = None
    if not data_not_ready:
        baseline = evaluate_baseline(
            tuple(_snapshot(candidate) for candidate in cohort_inputs),
            cash_usd=config.validation.starting_cash_usd,
            active_positions=int(active_position),
            policy=_strategy_policy(config),
            final_min=config.research.final_candidate_min,
            final_max=config.research.final_candidate_max,
        )
        evaluations = {row.symbol: row for row in baseline.research}
        ranking_rows: list[tuple[CandidateInput, CandidateDecision]] = []
        for candidate in cohort_inputs:
            evaluation = evaluations[candidate.symbol.upper()]
            decision = CandidateDecision(
                candidate.symbol,
                evaluation.eligible,
                evaluation.score or 0.0,
                (evaluation.reason,),
            )
            ranking_rows.append((candidate, decision))
            evidence[candidate.symbol].update(
                {
                    "eligible": evaluation.eligible,
                    "technical_score": evaluation.score,
                    "reasons": [evaluation.reason],
                }
            )
        for candidate, _, score in rank_all(ranking_rows):
            evidence[candidate.symbol]["rank_score"] = score if isfinite(score) else None
        finalist_payload = [_plan_payload(plan) for plan in baseline.finalists]
        primary = None if baseline.primary is None else _plan_payload(baseline.primary)
        baseline_no_trade_reason = baseline.no_trade_reason

    if active_position:
        no_trade_reason = "V1 already has an active position"
        decision_state = "NO_TRADE"
    elif data_not_ready:
        no_trade_reason = "decision data not ready: complete current-session inputs unavailable"
        decision_state = "DATA_NOT_READY"
    elif primary is None:
        no_trade_reason = baseline_no_trade_reason
        decision_state = "NO_TRADE"
    else:
        no_trade_reason = None
        decision_state = "PRIMARY"

    payload: dict[str, object] = {
        "engine_generated": True,
        "source": "production decision runner",
        "decision": "PRIMARY" if primary else "NO TRADE",
        "decision_state": decision_state,
        "session": session_key,
        "as_of": as_of.isoformat(),
        "algorithm": config.strategy.family,
        "software_version": config.project.software_version,
        "feature_version": FEATURE_VERSION,
        "starting_cash_usd": config.validation.starting_cash_usd,
        "active_position": active_position,
        "universe_size": len(current),
        "cohort_target": target,
        "cohort_size": len(cohort.members),
        "cohort_shortfall": cohort.shortfall,
        "input_rejections": rejected_inputs,
        "cohort": [evidence[member.symbol] for member in cohort.members],
        "finalists": finalist_payload,
        "primary": primary,
        "no_trade_reason": no_trade_reason,
    }
    report = SavedReport(
        snapshot_id=f"{session_key}-{uuid4().hex}",
        created_at=created,
        primary_symbol=None if primary is None else str(primary["symbol"]),
        payload=payload,
    )
    report_store.save_once(report)
    return report


def main(argv: list[str] | None = None) -> int:
    """Run the production decision command and return a process exit code."""
    parser = argparse.ArgumentParser(
        description="Build and persist the V1 production decision snapshot"
    )
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args(argv)

    try:
        config = load_config(args.root / "configs" / "v1.yaml")
        report = run_decision(
            config=config,
            market_store=MarketDataStore(args.root / "data" / "trading.db"),
            report_store=ReportStore(args.root / "data" / "decision_state.db"),
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"Decision run failed: {exc}")
        return 2

    outcome = report.primary_symbol or report.payload["no_trade_reason"]
    print(f"{report.payload['decision']}: {outcome}")
    print(f"Snapshot: {report.snapshot_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
