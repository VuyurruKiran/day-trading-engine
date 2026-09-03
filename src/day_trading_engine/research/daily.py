from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from day_trading_engine.ui.state import SavedReport


def _atomic_json(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def _status(plan: dict[str, object] | None, prices: list[float], key: str) -> str:
    if plan is None or key not in plan:
        return "unavailable"
    if not prices:
        return "unavailable"
    threshold = float(plan[key])
    return "observed" if (
        any(price >= threshold for price in prices)
        if key in {"entry", "target"}
        else any(price <= threshold for price in prices)
    ) else "not_observed"


def _quotes(
    root: Path, symbols: set[str], session: str, after: datetime
) -> dict[str, list[dict[str, object]]]:
    path = root / "data" / "trading.db"
    if not path.exists():
        return {}
    result: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in symbols}
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            SELECT symbol, last_trade_price, received_at, provider, session_phase,
                   delay_seconds, is_trade_eligible
            FROM market_quotes
            WHERE session_date = ? AND provider = 'questrade'
            ORDER BY received_at
            """,
            (session,),
        ).fetchall()
    for row in rows:
        symbol = str(row["symbol"]).upper()
        if symbol not in symbols:
            continue
        received_at = datetime.fromisoformat(str(row["received_at"]))
        if received_at < after:
            continue
        result[symbol].append(dict(row))
    return result


def _shadow_outcomes(root: Path, report: SavedReport) -> dict[str, dict[str, object]]:
    target = (
        root
        / "data"
        / "research"
        / datetime.fromisoformat(str(report.payload["session"])).strftime("%Y/%m")
        / f"{report.snapshot_id}.outcomes.parquet"
    )
    if not target.exists():
        return {}
    import pandas as pd

    outcomes: dict[str, dict[str, object]] = {}
    for row in pd.read_parquet(target).to_dict("records"):
        payload = json.loads(row["payload"])
        outcomes[str(row["symbol"]).upper()] = payload
    return outcomes


def _manual_status(root: Path, snapshot_id: str, symbol: str) -> tuple[bool, bool]:
    path = root / "data" / "decision_state.db"
    if not path.exists():
        return False, False
    with sqlite3.connect(path) as db:
        missed = db.execute(
            "SELECT 1 FROM decision_dispositions WHERE snapshot_id = ? AND symbol = ? "
            "AND status = 'missed_entry' LIMIT 1",
            (snapshot_id, symbol),
        ).fetchone()
        executed = db.execute(
            "SELECT 1 FROM manual_trades WHERE snapshot_id = ? AND symbol = ? LIMIT 1",
            (snapshot_id, symbol),
        ).fetchone()
    return missed is not None, executed is not None


def generate_daily_evaluation(root: Path, report: SavedReport) -> Path:
    """Persist an immutable Questrade planned-versus-observed cohort report."""
    session = report.payload.get("session")
    cohort = report.payload.get("cohort")
    if not isinstance(session, str) or not isinstance(cohort, list) or len(cohort) != 30:
        raise ValueError("daily evaluation requires a persisted 30-row cohort")
    if report.created_at.tzinfo is None or report.created_at.utcoffset() is None:
        raise ValueError("report created_at must be timezone-aware")

    rows = [row for row in cohort if isinstance(row, dict)]
    if len(rows) != 30:
        raise ValueError("daily evaluation cohort rows are invalid")
    symbols = {str(row["symbol"]).upper() for row in rows}
    quotes = _quotes(root, symbols, session, report.created_at.astimezone(UTC))
    outcomes = _shadow_outcomes(root, report)
    evaluations: list[dict[str, object]] = []
    for row in sorted(
        rows, key=lambda item: (int(item.get("cohort_rank", 0)), str(item["symbol"]))
    ):
        symbol = str(row["symbol"]).upper()
        plan = row.get("plan") if isinstance(row.get("plan"), dict) else None
        observed = quotes.get(symbol, [])
        prices = [
            float(item["last_trade_price"])
            for item in observed
            if item.get("last_trade_price") is not None and float(item["last_trade_price"]) > 0
        ]
        missed, executed = _manual_status(root, report.snapshot_id, symbol)
        evaluations.append(
            {
                "symbol": symbol,
                "role": (
                    "PRIMARY"
                    if row.get("primary") is True
                    else "FINALIST"
                    if row.get("finalist") is True
                    else "COHORT"
                ),
                "cohort_rank": row.get("cohort_rank"),
                "rank_score": row.get("rank_score"),
                "decision_price": (
                    (row.get("features") or {}).get("price")
                    if isinstance(row.get("features"), dict)
                    else None
                ),
                "plan": plan,
                "entry_status": _status(plan, prices, "entry"),
                "stop_status": _status(plan, prices, "stop"),
                "target_status": _status(plan, prices, "target"),
                "observed_min_price": min(prices) if prices else None,
                "observed_max_price": max(prices) if prices else None,
                "latest_price": prices[-1] if prices else None,
                "observation_count": len(observed),
                "observed_from": observed[0]["received_at"] if observed else None,
                "observed_to": observed[-1]["received_at"] if observed else None,
                "provider": "questrade" if observed else None,
                "shadow_outcome": outcomes.get(symbol),
                "missed_primary": missed and row.get("primary") is True,
                "manual_trade_executed": executed,
            }
        )

    payload = {
        "schema_version": "daily-evaluation-v1",
        "snapshot_id": report.snapshot_id,
        "session": session,
        "decision_at": report.created_at.isoformat(),
        "data_cutoff": max(
            (item["observed_to"] for item in evaluations if item["observed_to"]),
            default=None,
        ),
        "observation_provider": "Questrade",
        "shadow_provider": "Alpaca SIP",
        "ledger_effect": "none",
        "rows": evaluations,
    }
    target = (
        root / "data" / "research" / datetime.fromisoformat(session).strftime("%Y/%m")
        / f"{report.snapshot_id}.daily_evaluation.json"
    )
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") != encoded:
        raise ValueError("daily evaluation already exists with different data")
    if not target.exists():
        _atomic_json(target, payload)
    return target
