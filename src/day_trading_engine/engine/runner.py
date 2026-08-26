from __future__ import annotations

import argparse
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from uuid import uuid4

import pandas as pd

from day_trading_engine.core.config import AppConfig, load_config
from day_trading_engine.core.paths import project_root
from day_trading_engine.engine.cohort import ResearchCandidate, build_research_cohort
from day_trading_engine.engine.domain import CandidateDecision, CandidateInput, TradePlan
from day_trading_engine.engine.ranking import rank_all, shortlist
from day_trading_engine.engine.strategy import RiskPolicy, evaluate_candidate
from day_trading_engine.features.market import FEATURE_VERSION, build_market_features
from day_trading_engine.market_data.store import MarketDataStore, StoredQuote, parse_timestamp
from day_trading_engine.ui.state import ReportStore, SavedReport


def _build_candidate(
    store: MarketDataStore,
    latest: StoredQuote,
    *,
    as_of: datetime,
) -> tuple[CandidateInput | None, str | None, dict[str, object]]:
    session = store.session(latest.symbol, latest.received_at[:10])
    frame = pd.DataFrame([asdict(record) for record in session])
    features = build_market_features(frame, as_of=as_of) if not frame.empty else frame
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
        return None, "insufficient intraday samples to build complete features", {}

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


def _risk_policy(config: AppConfig) -> RiskPolicy:
    return RiskPolicy(
        max_spread_pct=config.risk.max_spread_pct,
        max_volatility=config.risk.max_volatility,
        min_volume=config.risk.min_volume,
        min_rvol=config.risk.min_rvol,
        reward_risk=config.strategy.reward_to_risk,
    )


def _plan_payload(plan: TradePlan, score: float) -> dict[str, object]:
    return {
        "symbol": plan.symbol,
        "score": score,
        "entry": plan.entry,
        "stop": plan.stop,
        "target": plan.target,
        "quantity": plan.quantity,
        "max_loss": plan.max_loss,
        "valid_from": plan.valid_from.isoformat(),
        "expires_at": plan.expires_at.isoformat(),
    }


def run_decision(
    *,
    config: AppConfig,
    market_store: MarketDataStore,
    report_store: ReportStore,
    created_at: datetime | None = None,
) -> SavedReport:
    latest = market_store.latest_all()
    if not latest:
        raise RuntimeError("no stored market quotes; run collect.ps1 first")

    as_of = max(parse_timestamp(record.received_at) for record in latest)
    session_key = as_of.date().isoformat()
    session_latest = tuple(
        record for record in latest if parse_timestamp(record.received_at).date() == as_of.date()
    )

    prepared: dict[str, tuple[CandidateInput | None, str | None, dict[str, object]]] = {}
    for record in session_latest:
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
        for record in session_latest
    ]
    cohort = build_research_cohort(
        discovery,
        session_key=session_key,
        target=config.research.daily_candidate_count,
        core_count=config.research.core_candidate_count,
        boundary_count=config.research.boundary_candidate_count,
    )

    active_position = report_store.has_open_execution()
    risk_policy = _risk_policy(config)
    evaluated: list[tuple[CandidateInput, CandidateDecision]] = []
    evidence: dict[str, dict[str, object]] = {}
    for member in cohort.members:
        candidate, _, features = prepared[member.symbol]
        if candidate is None:
            raise RuntimeError("cohort contains a candidate without complete features")
        decision = evaluate_candidate(
            candidate,
            cash=config.validation.starting_cash_usd,
            policy=risk_policy,
            active_position=active_position,
        )
        evaluated.append((candidate, decision))
        evidence[member.symbol] = {
            "symbol": member.symbol,
            "cohort_rank": member.rank,
            "cohort_bucket": member.bucket,
            "cohort_reason": member.reason,
            "features": features,
            "eligible": decision.eligible,
            "technical_score": decision.score,
            "reasons": list(decision.reasons),
            "plan": None if decision.plan is None else _plan_payload(decision.plan, decision.score),
        }

    ranked = rank_all(evaluated)
    for candidate, _, score in ranked:
        evidence[candidate.symbol]["rank_score"] = score if isfinite(score) else None

    finalists = shortlist(evaluated, limit=config.research.final_candidate_max)
    finalist_payload = [
        _plan_payload(decision.plan, score)
        for _, decision, score in finalists
        if decision.plan is not None
    ]
    if active_position:
        primary = None
        no_trade_reason = "V1 already has an active position"
    elif len(finalists) < config.research.final_candidate_min:
        primary = None
        no_trade_reason = "fewer than minimum trade-eligible finalists"
    else:
        _, decision, score = finalists[0]
        primary = None if decision.plan is None else _plan_payload(decision.plan, score)
        no_trade_reason = None

    rejected_inputs = [
        {"symbol": symbol, "reason": reason}
        for symbol, (candidate, reason, _) in prepared.items()
        if candidate is None
    ]
    created = created_at or datetime.now(UTC)
    if created.tzinfo is None or created.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")

    payload: dict[str, object] = {
        "engine_generated": True,
        "source": "production decision runner",
        "decision": "PRIMARY" if primary else "NO TRADE",
        "session": session_key,
        "as_of": as_of.isoformat(),
        "algorithm": config.strategy.family,
        "software_version": config.project.software_version,
        "feature_version": FEATURE_VERSION,
        "starting_cash_usd": config.validation.starting_cash_usd,
        "active_position": active_position,
        "cohort_target": config.research.daily_candidate_count,
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
