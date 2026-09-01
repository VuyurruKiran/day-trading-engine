from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, time, timedelta
from math import isfinite
from statistics import fmean
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from day_trading_engine.context.store import ContextStore
from day_trading_engine.core.config import AppConfig
from day_trading_engine.engine.cohort import CohortResult, ResearchCandidate, build_research_cohort
from day_trading_engine.engine.discovery import BroadScanScore
from day_trading_engine.engine.domain import CandidateDecision, CandidateInput
from day_trading_engine.engine.ranking import RankingWeights, rank_all
from day_trading_engine.engine.strategy import (
    CandidateSnapshot,
    StrategyPolicy,
    TradePlan,
    evaluate_baseline,
)
from day_trading_engine.engine.universe import UniverseSnapshot
from day_trading_engine.features.context import CONTEXT_FEATURE_VERSION, build_context_scores
from day_trading_engine.features.market import FEATURE_VERSION, build_market_features
from day_trading_engine.market_data.backfill import _session_bounds, _sessions
from day_trading_engine.market_data.store import MarketDataStore, StoredQuote, parse_timestamp
from day_trading_engine.research.store import ResearchStore
from day_trading_engine.ui.state import ReportStore, SavedReport

_MAX_QUOTE_AGE = timedelta(minutes=5)
_EASTERN = ZoneInfo("America/New_York")
_OPENING_RANGE = timedelta(minutes=5)
_OPENING_START_TOLERANCE = timedelta(minutes=1)
_INSUFFICIENT_FEATURES = "insufficient intraday samples to build complete features"
_OPENING_COVERAGE_MISSING = "regular-session opening-range coverage is incomplete"


def _regular_session_frame(session: tuple[StoredQuote, ...]) -> pd.DataFrame:
    frame = pd.DataFrame([asdict(record) for record in session])
    if frame.empty:
        return frame
    received = pd.to_datetime(frame["received_at"], utc=True, errors="raise")
    regular = received.map(_regular_session_timestamp)
    eligible = frame["is_trade_eligible"].eq(True)
    return frame.loc[regular & eligible].reset_index(drop=True)


def _has_opening_coverage(frame: pd.DataFrame) -> bool:
    if frame.empty:
        return False
    received = pd.to_datetime(frame["received_at"], utc=True, errors="raise").sort_values()
    first = received.iloc[0].to_pydatetime().astimezone(_EASTERN)
    open_at = first.replace(hour=9, minute=30, second=0, microsecond=0)
    if not (open_at <= first <= open_at + _OPENING_START_TOLERANCE):
        return False
    opening_end = open_at + _OPENING_RANGE
    opening = received[received.dt.tz_convert(_EASTERN) < pd.Timestamp(opening_end)]
    last = received.iloc[-1].to_pydatetime().astimezone(_EASTERN)
    return len(opening) >= 5 and last >= opening_end


def _market_score(candidate_return: float, benchmark_return: float) -> float:
    if not all(isfinite(value) for value in (candidate_return, benchmark_return)):
        raise ValueError("market-relative returns must be finite")
    # ponytail: +/-4% relative intraday performance is the frozen v1 normalization
    # ceiling; change it only through a versioned validation/refinement cycle.
    return min(1.0, max(0.0, 0.5 + (candidate_return - benchmark_return) / 0.08))


def _benchmark_return(
    store: MarketDataStore,
    symbols: tuple[str, ...],
    *,
    session_date: str,
    cutoff: datetime,
) -> float:
    returns: list[float] = []
    for symbol in symbols:
        latest = store.latest(symbol)
        if latest is None or not latest.is_trade_eligible:
            raise RuntimeError(f"critical market benchmark unavailable: {symbol}")
        received = parse_timestamp(latest.received_at)
        if received.astimezone(_EASTERN).date().isoformat() != session_date:
            raise RuntimeError(f"critical market benchmark is from another session: {symbol}")
        age = cutoff - received
        if age < timedelta(0) or age > _MAX_QUOTE_AGE:
            raise RuntimeError(f"critical market benchmark is stale: {symbol}")
        frame = _regular_session_frame(store.session(symbol, session_date))
        if frame.empty:
            raise RuntimeError(f"critical market benchmark history unavailable: {symbol}")
        received_at = pd.to_datetime(frame["received_at"], utc=True, errors="raise")
        frame = frame.loc[received_at <= pd.Timestamp(cutoff)]
        if len(frame) < 2:
            raise RuntimeError(f"critical market benchmark history incomplete: {symbol}")
        first = float(frame.iloc[0]["last_trade_price"])
        last = float(frame.iloc[-1]["last_trade_price"])
        if not isfinite(first) or not isfinite(last) or first <= 0 or last <= 0:
            raise RuntimeError(f"critical market benchmark price invalid: {symbol}")
        returns.append(last / first - 1.0)
    if not returns:
        raise RuntimeError("critical market benchmark set is empty")
    return fmean(returns)


def _sector_return(
    store: MarketDataStore,
    symbols: tuple[str, ...],
    *,
    session_date: str,
    cutoff: datetime,
    sector: str,
) -> float:
    returns: list[float] = []
    for symbol in symbols:
        latest = store.latest(symbol)
        if latest is None or not latest.is_trade_eligible:
            continue
        received = parse_timestamp(latest.received_at)
        age = cutoff - received
        if (
            received.astimezone(_EASTERN).date().isoformat() != session_date
            or age < timedelta(0)
            or age > _MAX_QUOTE_AGE
        ):
            continue
        frame = _regular_session_frame(store.session(symbol, session_date))
        if frame.empty:
            continue
        received_at = pd.to_datetime(frame["received_at"], utc=True, errors="raise")
        frame = frame.loc[received_at <= pd.Timestamp(cutoff)]
        if len(frame) < 2:
            continue
        first = float(frame.iloc[0]["last_trade_price"])
        last = float(frame.iloc[-1]["last_trade_price"])
        if isfinite(first) and isfinite(last) and first > 0 and last > 0:
            returns.append(last / first - 1.0)
    if not returns:
        raise RuntimeError(f"critical sector context unavailable: {sector}")
    return fmean(returns)


def _build_candidate(
    store: MarketDataStore,
    latest: StoredQuote,
    *,
    as_of: datetime,
    benchmark_return: float,
    sector_return: float | None = None,
) -> tuple[CandidateInput | None, str | None, dict[str, object]]:
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

    first_price = float(features.iloc[0]["last_trade_price"])
    last_price = float(row["last_trade_price"])
    candidate_return = last_price / first_price - 1.0
    benchmark_score = _market_score(candidate_return, benchmark_return)
    sector_score = None if sector_return is None else _market_score(candidate_return, sector_return)
    # ponytail: v3.1 defines one 20% market/sector component but no subweights;
    # use an equal deterministic blend until refinement versions a different split.
    normalized_market = (
        benchmark_score if sector_score is None else fmean((benchmark_score, sector_score))
    )
    evidence: dict[str, object] = {
        "received_at": str(row["received_at"]),
        "price": last_price,
        "bid": float(row["bid_price"]),
        "ask": float(row["ask_price"]),
        "volume": int(row["volume"]),
        "rvol": float(row["rvol"]),
        "vwap": float(row["vwap"]),
        "opening_range_high": float(row["opening_range_high"]),
        "opening_range_low": float(row["opening_range_low"]),
        "volatility": float(row["volatility"]),
        "spread_pct": float(row["spread_pct"]),
        "candidate_return": candidate_return,
        "benchmark_return": benchmark_return,
        "benchmark_score": benchmark_score,
        "sector_return": sector_return,
        "sector_score": sector_score,
        "market_score": normalized_market,
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
            market_score=normalized_market,
            delayed=latest.delay_seconds != 0,
            halted=latest.is_halted,
            provider_ok=latest.is_trade_eligible,
        ),
        None,
        evidence,
    )


def _strategy_policy(config: AppConfig) -> StrategyPolicy:
    return StrategyPolicy(
        max_spread_pct=config.risk.max_spread_pct,
        max_volatility=config.risk.max_volatility,
        min_rvol=config.risk.min_rvol,
        min_volume=config.risk.min_volume,
        entry_buffer_pct=config.strategy.entry_buffer_pct,
        stop_buffer_pct=config.strategy.stop_buffer_pct,
        reward_to_risk=config.strategy.reward_to_risk,
    )


def _ranking_weights(config: AppConfig) -> RankingWeights:
    return RankingWeights(
        technical=config.ranking.technical,
        market=config.ranking.market,
        news=config.ranking.news,
        social=config.ranking.reddit,
        fundamentals=config.ranking.fundamentals,
    )


def _available_cash(report_store: ReportStore, starting_cash: float) -> float:
    realized = sum(
        float(outcome.realized_pnl)
        for outcome in report_store.trade_outcome_history()
        if outcome.realized_pnl is not None
    )
    cash = starting_cash + realized
    if not isfinite(cash) or cash <= 0:
        raise RuntimeError("validation cash is depleted or invalid")
    return cash


def _snapshot(candidate: CandidateInput) -> CandidateSnapshot:
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


def _apply_context_scores(
    candidates: list[CandidateInput],
    *,
    evidence: dict[str, dict[str, object]],
    store: ContextStore,
    cutoff: datetime,
) -> list[CandidateInput]:
    records = store.as_of(cutoff)
    enriched: list[CandidateInput] = []
    for candidate in candidates:
        scores = build_context_scores(records, symbol=candidate.symbol, cutoff=cutoff)
        updated = replace(
            candidate,
            news_score=scores.news,
            social_score=scores.reddit,
            fundamental_score=scores.fundamentals,
        )
        evidence[candidate.symbol]["context"] = {
            "feature_version": CONTEXT_FEATURE_VERSION,
            "market_score": candidate.market_score,
            "macro_score": scores.macro,
            "news_score": scores.news,
            "social_score": scores.reddit,
            "fundamental_score": scores.fundamentals,
            "evidence_counts": scores.evidence_counts,
        }
        enriched.append(updated)
    return enriched


def _select_current_universe(
    latest: tuple[StoredQuote, ...], *, symbols: tuple[str, ...]
) -> tuple[StoredQuote, ...]:
    by_symbol = {record.symbol.upper(): record for record in latest}
    return tuple(by_symbol[symbol.upper()] for symbol in symbols if symbol.upper() in by_symbol)


def _regular_session_timestamp(value: datetime) -> bool:
    eastern = value.astimezone(_EASTERN)
    session = eastern.date()
    if session not in _sessions(session, session):
        return False
    session_open, session_close = _session_bounds(session)
    return session_open <= eastern.time() < session_close


def _validate_configured_decision_time(created: datetime, config: AppConfig) -> None:
    local = created.astimezone(ZoneInfo(config.project.timezone))
    configured = time.fromisoformat(config.project.decision_time)
    if local.time().replace(tzinfo=None) < configured:
        raise RuntimeError("decision run is before the configured daily decision time")


def _validate_decision_time(as_of: datetime, created: datetime) -> None:
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
    frozen_cohort: CohortResult | None = None,
    broad_scan_scores: tuple[BroadScanScore, ...] | None = None,
    universe_snapshot: UniverseSnapshot | None = None,
) -> SavedReport:
    latest = market_store.latest_all()
    if not latest:
        raise RuntimeError("no stored market quotes; run collect.ps1 first")

    created = created_at or datetime.now(UTC)
    if created.tzinfo is None or created.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    _validate_configured_decision_time(created, config)

    target = config.research.daily_candidate_count
    current = _select_current_universe(latest, symbols=config.market_data.watchlist)
    minimum_current = len(frozen_cohort.members) if frozen_cohort is not None else target
    if len(current) < minimum_current:
        raise RuntimeError(f"decision universe incomplete: need {minimum_current} current symbols")

    as_of = max(parse_timestamp(record.received_at) for record in current)
    _validate_decision_time(as_of, created)
    _validate_universe_freshness(current, created)
    session_key = as_of.astimezone(_EASTERN).date().isoformat()
    benchmark_return = _benchmark_return(
        market_store,
        config.research_universe.benchmark_symbols,
        session_date=session_key,
        cutoff=created,
    )
    cash_usd = _available_cash(report_store, config.validation.starting_cash_usd)

    identity_by_symbol = (
        {} if universe_snapshot is None else {row.symbol: row for row in universe_snapshot.members}
    )
    sector_returns: dict[str, float] = {}
    if universe_snapshot is not None:
        missing_identity = [row.symbol for row in current if row.symbol not in identity_by_symbol]
        if missing_identity:
            raise RuntimeError(
                "selected symbols are missing from versioned universe snapshot: "
                + ", ".join(missing_identity)
            )
        sectors = {
            identity_by_symbol[row.symbol].sector
            for row in current
            if identity_by_symbol[row.symbol].sector != "UNKNOWN"
        }
        for sector in sectors:
            peer_symbols = tuple(
                row.symbol for row in universe_snapshot.members if row.sector == sector
            )
            sector_returns[sector] = _sector_return(
                market_store,
                peer_symbols,
                session_date=session_key,
                cutoff=created,
                sector=sector,
            )

    prepared: dict[str, tuple[CandidateInput | None, str | None, dict[str, object]]] = {}
    for record in current:
        if not record.is_trade_eligible:
            prepared[record.symbol] = (None, record.invalid_reason or "invalid market quote", {})
            continue
        identity = identity_by_symbol.get(record.symbol)
        prepared[record.symbol] = _build_candidate(
            market_store,
            record,
            as_of=as_of,
            benchmark_return=benchmark_return,
            sector_return=(
                None
                if identity is None or identity.sector == "UNKNOWN"
                else sector_returns[identity.sector]
            ),
        )

    if frozen_cohort is None:
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
        assert isinstance(cohort, CohortResult)
    else:
        cohort = frozen_cohort
        cohort_symbols = tuple(member.symbol for member in cohort.members)
        if (
            len(cohort_symbols) > target
            or len(cohort_symbols) != len(set(cohort_symbols))
            or cohort.shortfall != target - len(cohort_symbols)
            or set(cohort_symbols) != set(config.market_data.watchlist)
        ):
            raise ValueError("frozen research cohort does not match the decision watchlist")

    broad_rows = broad_scan_scores or ()
    broad_by_symbol = {row.symbol: row for row in broad_rows}
    broad_rank = {row.symbol: rank for rank, row in enumerate(broad_rows, start=1)}

    active_position = report_store.has_open_execution()
    cohort_inputs: list[CandidateInput] = []
    evidence: dict[str, dict[str, object]] = {}
    for member in cohort.members:
        candidate, reason, features = prepared[member.symbol]
        identity = identity_by_symbol.get(member.symbol)
        broad = broad_by_symbol.get(member.symbol)
        row: dict[str, object] = {
            "session": session_key,
            "symbol": member.symbol,
            "security_id": None if identity is None else identity.security_id,
            "universe_id": None if universe_snapshot is None else universe_snapshot.universe_id,
            "sector": None if identity is None else identity.sector,
            "broad_rank": broad_rank.get(member.symbol),
            "broad_score": None if broad is None else broad.score,
            "broad_components": {} if broad is None else broad.components,
            "broad_reason": None if broad is None else broad.reason,
            "cohort_rank": member.rank,
            "cohort_bucket": member.bucket,
            "cohort_reason": member.reason,
            "features": features,
            "finalist": False,
            "primary": False,
        }
        if candidate is None:
            row.update(
                {
                    "eligible": False,
                    "technical_score": None,
                    "rank_score": None,
                    "reasons": [reason or "current-session candidate inputs unavailable"],
                    "plan": None,
                }
            )
        else:
            cohort_inputs.append(candidate)
        evidence[member.symbol] = row

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
        with ContextStore(report_store.path.parent / "context.db") as context_store:
            cohort_inputs = _apply_context_scores(
                cohort_inputs,
                evidence=evidence,
                store=context_store,
                cutoff=created,
            )
        technical = evaluate_baseline(
            tuple(_snapshot(candidate) for candidate in cohort_inputs),
            cash_usd=cash_usd,
            active_positions=int(active_position),
            policy=_strategy_policy(config),
            final_min=config.research.final_candidate_min,
            final_max=config.research.final_candidate_max,
        )
        evaluations = {row.symbol: row for row in technical.research}
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
                    "plan": None if evaluation.plan is None else _plan_payload(evaluation.plan),
                }
            )

        rank_scores: dict[str, float] = {}
        for candidate, decision, score in rank_all(
            ranking_rows, weights=_ranking_weights(config)
        ):
            evidence[candidate.symbol]["rank_score"] = score if isfinite(score) else None
            if decision.eligible:
                rank_scores[candidate.symbol] = score

        baseline = evaluate_baseline(
            tuple(_snapshot(candidate) for candidate in cohort_inputs),
            cash_usd=cash_usd,
            active_positions=int(active_position),
            policy=_strategy_policy(config),
            final_min=config.research.final_candidate_min,
            final_max=config.research.final_candidate_max,
            rank_scores=rank_scores,
            minimum_rank_score=config.ranking.minimum_final_score,
        )
        finalist_payload = [_plan_payload(plan) for plan in baseline.finalists]
        primary = None if baseline.primary is None else _plan_payload(baseline.primary)
        baseline_no_trade_reason = baseline.no_trade_reason
        finalist_symbols = {plan["symbol"] for plan in finalist_payload}
        primary_symbol = None if primary is None else primary["symbol"]
        for symbol, row in evidence.items():
            row["finalist"] = symbol in finalist_symbols
            row["primary"] = symbol == primary_symbol

    if active_position:
        no_trade_reason = "V1 already has an active position"
        decision_state = "NO_TRADE"
    elif data_not_ready:
        no_trade_reason = "decision data not ready: research cohort shortfall"
        decision_state = "DATA_NOT_READY"
    elif primary is None:
        no_trade_reason = baseline_no_trade_reason or (
            "zero candidates met the final score threshold"
        )
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
        "ranking_version": config.ranking.normalization_version,
        "starting_cash_usd": config.validation.starting_cash_usd,
        "available_cash_usd": cash_usd,
        "active_position": active_position,
        "benchmark_symbols": list(config.research_universe.benchmark_symbols),
        "universe_id": None if universe_snapshot is None else universe_snapshot.universe_id,
        "universe_effective_from": (
            None if universe_snapshot is None else universe_snapshot.effective_from
        ),
        "universe_checksum": None if universe_snapshot is None else universe_snapshot.checksum,
        "universe_selector_version": (
            None if universe_snapshot is None else universe_snapshot.selector_version
        ),
        "universe_size": (
            len(current) if universe_snapshot is None else len(universe_snapshot.members)
        ),
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
    saved = report_store.save_once(report)
    rows = saved.payload.get("cohort")
    if isinstance(rows, list) and len(rows) == target:
        ResearchStore(report_store.path.parent / "research.db").save_decision_rows(
            saved.snapshot_id,
            [dict(row) for row in rows if isinstance(row, dict)],
        )
    return saved


def main(argv: list[str] | None = None) -> int:
    from day_trading_engine.engine.live import main as live_main

    return live_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
