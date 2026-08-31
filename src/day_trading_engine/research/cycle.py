from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import fmean, median

import pandas as pd

_COMPONENTS = ("technical", "market", "news", "reddit", "fundamentals")
_VARIANTS = {
    "A_TECHNICAL": ("technical",),
    "B_TECH_MARKET": ("technical", "market"),
    "C_PLUS_NEWS": ("technical", "market", "news"),
    "D_PLUS_REDDIT": ("technical", "market", "news", "reddit"),
    "E_FULL": _COMPONENTS,
}


def _bounded(value: object, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def classify_regimes(row: dict[str, object]) -> dict[str, str]:
    """Classify deterministic decision-time regimes without using future labels."""
    features = row.get("features")
    features = features if isinstance(features, dict) else {}
    context = row.get("context")
    context = context if isinstance(context, dict) else {}

    market = _bounded(context.get("market_score", features.get("market_score")))
    volatility = _bounded(features.get("volatility_score"))
    if volatility >= 0.75:
        market_regime = "HIGH_VOLATILITY"
    elif volatility <= 0.25:
        market_regime = "LOW_VOLATILITY"
    elif market >= 0.62:
        market_regime = "BULLISH_TREND"
    elif market <= 0.38:
        market_regime = "BEARISH_TREND"
    else:
        market_regime = "RANGE"

    gap = float(features.get("gap_pct", 0.0) or 0.0)
    momentum = _bounded(features.get("momentum_score"))
    rvol = float(features.get("rvol", 1.0) or 1.0)
    sector = _bounded(features.get("sector_score", context.get("sector_score")))
    if gap >= 0.02:
        stock_regime = "GAP_UP"
    elif gap <= -0.02:
        stock_regime = "GAP_DOWN"
    elif rvol >= 2.0:
        stock_regime = "HIGH_RVOL"
    elif momentum >= 0.65:
        stock_regime = "MOMENTUM"
    elif momentum <= 0.35:
        stock_regime = "MEAN_REVERTING"
    elif sector >= 0.62:
        stock_regime = "SECTOR_LEADERSHIP"
    elif sector <= 0.38:
        stock_regime = "SECTOR_LAGGING"
    else:
        stock_regime = "NEUTRAL"

    evidence = context.get("evidence_counts")
    evidence = evidence if isinstance(evidence, dict) else {}
    if int(evidence.get("earnings", 0) or 0) > 0:
        catalyst_regime = "EARNINGS"
    elif int(evidence.get("sec", 0) or 0) > 0:
        catalyst_regime = "FILING"
    elif int(evidence.get("news", 0) or 0) > 0:
        catalyst_regime = "COMPANY_NEWS"
    elif int(evidence.get("macro", 0) or 0) > 0:
        catalyst_regime = "MACRO_HEAVY"
    else:
        catalyst_regime = "NO_MATERIAL_CATALYST"

    spread = float(features.get("spread_pct", 0.0) or 0.0)
    missing = [
        name
        for name in ("news_score", "social_score", "fundamental_score")
        if context.get(name) is None
    ]
    if row.get("eligible") is not True:
        data_regime = "HARD_GATE_REJECTED"
    elif spread >= 0.01:
        data_regime = "WIDE_SPREAD"
    elif missing:
        data_regime = "MISSING_CONTEXT"
    else:
        data_regime = "COMPLETE_TIGHT_SPREAD"

    return {
        "version": "regime-v1",
        "market": market_regime,
        "stock": stock_regime,
        "catalyst": catalyst_regime,
        "execution_data": data_regime,
    }


@dataclass(frozen=True)
class PromotionEvidence:
    experiment_id: str
    challenger_id: str
    champion_id: str
    complete_sessions: int
    triggered_setups: int
    expectancy: float
    champion_expectancy: float
    max_drawdown: float
    champion_drawdown: float
    reproducible: bool
    forward_confirmed: bool
    hard_risk_regression: bool = False
    dominated: bool = False
    fragile: bool = False


def promotion_result(evidence: PromotionEvidence, *, minimum_sessions: int = 15) -> str:
    """Return PROMOTED only when every mandatory v3.1 gate passes."""
    gates = (
        evidence.complete_sessions >= minimum_sessions,
        evidence.triggered_setups > 0,
        evidence.expectancy >= evidence.champion_expectancy,
        evidence.max_drawdown <= evidence.champion_drawdown,
        evidence.reproducible,
        evidence.forward_confirmed,
        not evidence.hard_risk_regression,
        not evidence.dominated,
        not evidence.fragile,
    )
    return "PROMOTED" if all(gates) else "NO CHANGE"


class ResearchRegistry:
    """Small SQLite registry for reproducible datasets and challenger decisions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_version TEXT PRIMARY KEY,
                    manifest_hash TEXT NOT NULL,
                    date_range TEXT NOT NULL,
                    universe_versions TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS algorithm_versions (
                    algorithm_id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    created_at TEXT NOT NULL,
                    git_commit TEXT NOT NULL,
                    config_version TEXT NOT NULL,
                    feature_version TEXT NOT NULL,
                    weights TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    hypothesis TEXT NOT NULL,
                    champion TEXT NOT NULL,
                    challenger TEXT NOT NULL,
                    train_period TEXT NOT NULL,
                    validation_period TEXT NOT NULL,
                    holdout_period TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiment_results (
                    experiment_id TEXT NOT NULL,
                    dataset_version TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    regime_metrics TEXT NOT NULL,
                    data_quality TEXT NOT NULL,
                    result TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, dataset_version)
                );
                CREATE TABLE IF NOT EXISTS holdouts (
                    period TEXT NOT NULL,
                    dataset_version TEXT NOT NULL,
                    used_by TEXT,
                    used_at TEXT,
                    status TEXT NOT NULL,
                    PRIMARY KEY(period, dataset_version)
                );
                CREATE TABLE IF NOT EXISTS champion_cycles (
                    cycle_id TEXT PRIMARY KEY,
                    champion_id TEXT NOT NULL,
                    promoted_experiment_id TEXT,
                    result TEXT NOT NULL,
                    decided_at TEXT NOT NULL
                );
                """
            )

    def register_dataset(
        self,
        dataset_version: str,
        *,
        manifest_hash: str,
        date_range: str,
        universe_versions: list[str],
        schema_version: str,
    ) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO datasets VALUES (?, ?, ?, ?, ?, ?)",
                (
                    dataset_version,
                    manifest_hash,
                    date_range,
                    json.dumps(sorted(set(universe_versions))),
                    schema_version,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def register_algorithm(
        self,
        algorithm_id: str,
        *,
        parent_id: str | None,
        git_commit: str,
        config_version: str,
        feature_version: str,
        weights: dict[str, float],
        status: str,
    ) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO algorithm_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    algorithm_id,
                    parent_id,
                    datetime.now(UTC).isoformat(),
                    git_commit,
                    config_version,
                    feature_version,
                    json.dumps(weights, sort_keys=True),
                    status,
                ),
            )

    def consume_holdout(self, period: str, dataset_version: str, experiment_id: str) -> None:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT status FROM holdouts WHERE period = ? AND dataset_version = ?",
                (period, dataset_version),
            ).fetchone()
            if row is not None and row[0] == "USED":
                raise ValueError("holdout has already influenced a decision")
            db.execute(
                "INSERT OR REPLACE INTO holdouts VALUES (?, ?, ?, ?, 'USED')",
                (period, dataset_version, experiment_id, datetime.now(UTC).isoformat()),
            )

    def decide_cycle(self, cycle_id: str, evidence: PromotionEvidence) -> str:
        result = promotion_result(evidence)
        with sqlite3.connect(self.path) as db:
            current = db.execute(
                "SELECT promoted_experiment_id FROM champion_cycles WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchone()
            if current is not None and current[0] is not None:
                return "NO CHANGE"
            db.execute(
                "INSERT OR REPLACE INTO champion_cycles VALUES (?, ?, ?, ?, ?)",
                (
                    cycle_id,
                    evidence.challenger_id if result == "PROMOTED" else evidence.champion_id,
                    evidence.experiment_id if result == "PROMOTED" else None,
                    result,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return result

    def summary(self) -> dict[str, object]:
        with sqlite3.connect(self.path) as db:
            counts = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "datasets",
                    "algorithm_versions",
                    "experiments",
                    "experiment_results",
                    "holdouts",
                    "champion_cycles",
                )
            }
            recent = db.execute(
                "SELECT cycle_id, champion_id, result, decided_at "
                "FROM champion_cycles ORDER BY decided_at DESC LIMIT 5"
            ).fetchall()
        return {
            "counts": counts,
            "recent_cycles": [
                {
                    "cycle_id": row[0],
                    "champion_id": row[1],
                    "result": row[2],
                    "decided_at": row[3],
                }
                for row in recent
            ],
        }


def _read_month(root: Path, month: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    year, month_number = month.split("-", 1)
    directory = root / "data" / "research" / year / month_number
    candidates: list[dict[str, object]] = []
    outcomes: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.candidates.parquet")):
        frame = pd.read_parquet(path)
        for row in frame.to_dict("records"):
            payload = json.loads(row["payload"])
            payload["snapshot_id"] = row["snapshot_id"]
            candidates.append(payload)
    for path in sorted(directory.glob("*.outcomes.parquet")):
        frame = pd.read_parquet(path)
        for row in frame.to_dict("records"):
            payload = json.loads(row["payload"])
            payload["snapshot_id"] = row["snapshot_id"]
            payload["symbol"] = row["symbol"]
            outcomes.append(payload)
    return candidates, outcomes


def _component_values(row: dict[str, object]) -> dict[str, float]:
    context = row.get("context")
    context = context if isinstance(context, dict) else {}
    features = row.get("features")
    features = features if isinstance(features, dict) else {}
    return {
        "technical": _bounded(row.get("technical_score")),
        "market": _bounded(context.get("market_score", features.get("market_score"))),
        "news": _bounded(context.get("news_score")),
        "reddit": _bounded(context.get("social_score")),
        "fundamentals": _bounded(context.get("fundamental_score")),
    }


def _variant_score(row: dict[str, object], components: tuple[str, ...]) -> float:
    values = _component_values(row)
    weights = {
        "technical": 0.50,
        "market": 0.20,
        "news": 0.20,
        "reddit": 0.05,
        "fundamentals": 0.05,
    }
    denominator = sum(weights[name] for name in components)
    return sum(values[name] * weights[name] for name in components) / denominator


def _drawdown(returns: list[float]) -> float:
    equity = peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return worst


def _metrics(returns: list[float], outcomes: list[dict[str, object]]) -> dict[str, object]:
    triggered = [row for row in outcomes if row.get("entry_triggered") is True]
    wins = [row for row in triggered if row.get("target_before_stop") is True]
    return {
        "count": len(returns),
        "triggered": len(triggered),
        "expectancy": fmean(returns) if returns else 0.0,
        "median_return": median(returns) if returns else 0.0,
        "hit_rate": len(wins) / len(triggered) if triggered else 0.0,
        "max_drawdown": _drawdown(returns),
        "mean_mfe_pct": fmean(float(row.get("mfe_pct", 0.0)) for row in outcomes)
        if outcomes
        else 0.0,
        "mean_mae_pct": fmean(float(row.get("mae_pct", 0.0)) for row in outcomes)
        if outcomes
        else 0.0,
    }


def build_ablation_report(
    candidates: list[dict[str, object]],
    outcomes: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Compare component variants using realized shadow outcomes, grouped by session."""
    outcome_by_key = {
        (str(row.get("snapshot_id")), str(row.get("symbol"))): row for row in outcomes
    }
    sessions: dict[str, list[dict[str, object]]] = {}
    for row in candidates:
        if row.get("eligible") is True and isinstance(row.get("plan"), dict):
            sessions.setdefault(str(row.get("session")), []).append(row)

    reports: list[dict[str, object]] = []
    for name, components in _VARIANTS.items():
        selected_outcomes: list[dict[str, object]] = []
        returns: list[float] = []
        top_k_captures = 0
        comparable = 0
        for rows in sessions.values():
            ranked = sorted(
                rows,
                key=lambda row: (-_variant_score(row, components), str(row.get("symbol"))),
            )
            available = [
                (row, outcome_by_key.get((str(row.get("snapshot_id")), str(row.get("symbol")))))
                for row in ranked
            ]
            available = [(row, outcome) for row, outcome in available if outcome is not None]
            if not available:
                continue
            primary, primary_outcome = available[0]
            selected_outcomes.append(primary_outcome)
            primary_return = float(primary_outcome.get("shadow_return", 0.0) or 0.0)
            returns.append(primary_return)
            realized = [float(outcome.get("shadow_return", 0.0) or 0.0) for _, outcome in available]
            best = max(realized)
            comparable += 1
            top_k = available[:5]
            if any(float(outcome.get("shadow_return", 0.0) or 0.0) == best for _, outcome in top_k):
                top_k_captures += 1
            _ = primary
        metrics = _metrics(returns, selected_outcomes)
        metrics.update(
            {
                "variant": name,
                "components": list(components),
                "top5_capture_rate": top_k_captures / comparable if comparable else 0.0,
            }
        )
        reports.append(metrics)
    return reports


def generate_monthly_report(root: Path, month: str) -> Path:
    """Generate immutable-input monthly evidence and register its dataset version."""
    candidates, outcomes = _read_month(root, month)
    ablations = build_ablation_report(candidates, outcomes)
    sessions = sorted({str(row.get("session")) for row in candidates})
    universe_versions = sorted(
        {str(row.get("universe_id")) for row in candidates if row.get("universe_id")}
    )
    manifest_basis = {
        "month": month,
        "sessions": sessions,
        "candidate_rows": len(candidates),
        "outcome_rows": len(outcomes),
        "universe_versions": universe_versions,
        "snapshot_ids": sorted({str(row.get("snapshot_id")) for row in candidates}),
    }
    manifest_hash = sha256(
        json.dumps(manifest_basis, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    dataset_version = f"{month}-{manifest_hash[:12]}"

    regime_counts: dict[str, dict[str, int]] = {}
    for row in outcomes:
        regimes = row.get("regimes")
        if not isinstance(regimes, dict):
            continue
        for family in ("market", "stock", "catalyst", "execution_data"):
            label = str(regimes.get(family, "UNKNOWN"))
            counts = regime_counts.setdefault(family, {})
            counts[label] = counts.get(label, 0) + 1

    report = {
        "month": month,
        "dataset_version": dataset_version,
        "manifest_hash": manifest_hash,
        "generated_at": datetime.now(UTC).isoformat(),
        "data_quality": {
            "sessions": len(sessions),
            "candidate_rows": len(candidates),
            "outcome_rows": len(outcomes),
            "complete_30_sessions": sum(
                1 for session in sessions if sum(row.get("session") == session for row in candidates) == 30
            ),
        },
        "universe_versions": universe_versions,
        "ablations": ablations,
        "regime_breakdown": regime_counts,
        "promotion_policy": {
            "automatic_promotion": False,
            "maximum_promotions_per_cycle": 1,
            "default_result": "NO CHANGE",
        },
    }
    directory = root / "data" / "research" / month[:4] / month[5:]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "monthly_report.json"
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") != encoded:
        raise ValueError("monthly report already exists with different data")
    target.write_text(encoded, encoding="utf-8")
    pd.DataFrame(ablations).to_parquet(directory / "ablations.parquet", index=False)

    registry = ResearchRegistry(root / "data" / "research.db")
    registry.register_dataset(
        dataset_version,
        manifest_hash=manifest_hash,
        date_range=f"{sessions[0]}..{sessions[-1]}" if sessions else month,
        universe_versions=universe_versions,
        schema_version="v3.1",
    )
    return target
