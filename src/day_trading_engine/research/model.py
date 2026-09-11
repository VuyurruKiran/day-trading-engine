from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import exp, isfinite, log

DEFAULT_FEATURE_NAMES = (
    "market_score",
    "volatility_score",
    "gap_pct",
    "rvol",
    "spread_pct",
)


def _sigmoid(value: float) -> float:
    if value >= 0:
        scaled = exp(-value)
        return 1.0 / (1.0 + scaled)
    scaled = exp(value)
    return scaled / (1.0 + scaled)


@dataclass(frozen=True, slots=True)
class LogisticBaseline:
    """Small deterministic regularized logistic ranking baseline."""

    feature_names: tuple[str, ...]
    weights: tuple[float, ...]
    intercept: float
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]
    algorithm_version: str = "logistic-baseline-v1"

    @classmethod
    def fit(
        cls,
        rows: Sequence[Mapping[str, float]],
        labels: Sequence[int | bool],
        *,
        feature_names: Sequence[str],
        epochs: int = 500,
        learning_rate: float = 0.05,
        l2: float = 0.01,
    ) -> LogisticBaseline:
        names = tuple(feature_names)
        if not names or len(rows) != len(labels) or not rows:
            raise ValueError("training rows, labels, and features are required")
        if epochs < 1 or learning_rate <= 0 or l2 < 0:
            raise ValueError("invalid logistic training parameters")
        matrix = [[float(row[name]) for name in names] for row in rows]
        if any(not isfinite(value) for row in matrix for value in row):
            raise ValueError("training features must be finite")
        target = [int(value) for value in labels]
        if any(value not in (0, 1) for value in target):
            raise ValueError("training labels must be binary")
        means = tuple(
            sum(row[index] for row in matrix) / len(matrix) for index in range(len(names))
        )
        scales = tuple(
            max(
                1e-12,
                (sum((row[index] - means[index]) ** 2 for row in matrix) / len(matrix)) ** 0.5,
            )
            for index in range(len(names))
        )
        normalized = [
            [(value - means[index]) / scales[index] for index, value in enumerate(row)]
            for row in matrix
        ]
        weights = [0.0] * len(names)
        intercept = 0.0
        for _ in range(epochs):
            errors = [
                _sigmoid(
                    intercept
                    + sum(weight * value for weight, value in zip(weights, row, strict=True))
                )
                - label
                for row, label in zip(normalized, target, strict=True)
            ]
            intercept -= learning_rate * sum(errors) / len(errors)
            for index in range(len(weights)):
                gradient = sum(
                    error * row[index] for error, row in zip(errors, normalized, strict=True)
                ) / len(errors)
                weights[index] -= learning_rate * (gradient + l2 * weights[index])
        return cls(names, tuple(weights), intercept, means, scales)

    def predict_proba(self, row: Mapping[str, float]) -> float:
        values = []
        for index, name in enumerate(self.feature_names):
            try:
                value = float(row[name])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"missing or invalid feature: {name}") from exc
            if not isfinite(value):
                raise ValueError(f"feature must be finite: {name}")
            values.append((value - self.feature_means[index]) / self.feature_scales[index])
        return _sigmoid(
            self.intercept
            + sum(weight * value for weight, value in zip(self.weights, values, strict=True))
        )

    def feature_importance(self) -> dict[str, float]:
        return dict(zip(self.feature_names, self.weights, strict=True))


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    windows: tuple[dict[str, dict[str, float]], ...]
    algorithm_version: str = "logistic-baseline-v1"


def evaluate_walk_forward(
    rows: Sequence[Mapping[str, float]],
    labels: Sequence[int | bool],
    *,
    feature_names: Sequence[str],
    train_size: int,
    validation_size: int,
    holdout_size: int,
) -> WalkForwardResult:
    """Train only on each past window and score later validation/holdout rows."""
    if len(rows) != len(labels):
        raise ValueError("rows and labels must be aligned")
    windows = []
    for train, validation, holdout in walk_forward_splits(
        len(rows),
        train_size=train_size,
        validation_size=validation_size,
        holdout_size=holdout_size,
    ):
        model = LogisticBaseline.fit(
            [rows[index] for index in train],
            [labels[index] for index in train],
            feature_names=feature_names,
        )
        validation_probabilities = [
            model.predict_proba(rows[index]) for index in validation
        ]
        holdout_probabilities = [model.predict_proba(rows[index]) for index in holdout]
        windows.append(
            {
                "validation": binary_metrics(
                    validation_probabilities,
                    [labels[index] for index in validation],
                ),
                "holdout": binary_metrics(
                    holdout_probabilities,
                    [labels[index] for index in holdout],
                ),
            }
        )
    return WalkForwardResult(tuple(windows))


def walk_forward_splits(
    row_count: int,
    *,
    train_size: int,
    validation_size: int,
    holdout_size: int,
) -> tuple[tuple[range, range, range], ...]:
    """Return non-overlapping chronological train/validation/holdout windows."""
    if min(row_count, train_size, validation_size, holdout_size) < 1:
        raise ValueError("split sizes must be positive")
    windows = []
    start = 0
    while start + train_size + validation_size + holdout_size <= row_count:
        train_end = start + train_size
        validation_end = train_end + validation_size
        holdout_end = validation_end + holdout_size
        windows.append(
            (
                range(start, train_end),
                range(train_end, validation_end),
                range(validation_end, holdout_end),
            )
        )
        start += validation_size
    if not windows:
        raise ValueError("row_count cannot satisfy requested split sizes")
    return tuple(windows)


def binary_metrics(
    probabilities: Sequence[float], labels: Sequence[int | bool]
) -> dict[str, float]:
    if len(probabilities) != len(labels) or not probabilities:
        raise ValueError("probabilities and labels must be non-empty and aligned")
    values = [float(value) for value in probabilities]
    target = [int(value) for value in labels]
    if any(not isfinite(value) or not 0 <= value <= 1 for value in values):
        raise ValueError("probabilities must be finite and in [0,1]")
    if any(value not in (0, 1) for value in target):
        raise ValueError("labels must be binary")
    clipped = [min(1 - 1e-12, max(1e-12, value)) for value in values]
    return {
        "log_loss": -sum(
            label * log(prob) + (1 - label) * log(1 - prob)
            for prob, label in zip(clipped, target, strict=True)
        )
        / len(target),
        "brier": sum((prob - label) ** 2 for prob, label in zip(values, target, strict=True))
        / len(target),
        "accuracy": sum(
            (prob >= 0.5) == bool(label) for prob, label in zip(values, target, strict=True)
        )
        / len(target),
    }


def build_model_evidence(
    candidates: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
    *,
    feature_names: Sequence[str] = DEFAULT_FEATURE_NAMES,
    train_size: int = 30,
    validation_size: int = 10,
    holdout_size: int = 10,
) -> dict[str, object]:
    """Build a walk-forward report from persisted point-in-time rows.

    Rows with missing features or unknown outcomes are excluded and counted;
    they are never replaced with neutral values or fabricated labels.
    """
    names = tuple(feature_names)
    outcome_by_key = {
        (str(row.get("snapshot_id")), str(row.get("symbol"))): row
        for row in outcomes
    }
    usable: list[tuple[str, str, dict[str, float], int]] = []
    excluded = {"ineligible": 0, "missing_features": 0, "unknown_outcome": 0}
    ordered = sorted(
        candidates,
        key=lambda row: (
            str(row.get("session")),
            str(row.get("snapshot_id")),
            str(row.get("symbol")),
        ),
    )
    for candidate in ordered:
        if candidate.get("eligible") is not True:
            excluded["ineligible"] += 1
            continue
        features = candidate.get("features")
        if not isinstance(features, Mapping):
            excluded["missing_features"] += 1
            continue
        try:
            values = {name: float(features[name]) for name in names}
        except (KeyError, TypeError, ValueError):
            excluded["missing_features"] += 1
            continue
        if any(not isfinite(value) for value in values.values()):
            excluded["missing_features"] += 1
            continue
        outcome = outcome_by_key.get(
            (str(candidate.get("snapshot_id")), str(candidate.get("symbol")))
        )
        if outcome is None or outcome.get("net_return") is None:
            excluded["unknown_outcome"] += 1
            continue
        try:
            label = int(float(outcome["net_return"]) > 0)
        except (TypeError, ValueError):
            excluded["unknown_outcome"] += 1
            continue
        usable.append((str(candidate.get("session")), str(candidate.get("symbol")), values, label))

    minimum = train_size + validation_size + holdout_size
    report: dict[str, object] = {
        "algorithm_version": "logistic-baseline-v1",
        "feature_names": list(names),
        "rows_considered": len(candidates),
        "rows_used": len(usable),
        "excluded": excluded,
        "train_size": train_size,
        "validation_size": validation_size,
        "holdout_size": holdout_size,
    }
    if len(usable) < minimum:
        report.update(
            {
                "status": "INSUFFICIENT_DATA",
                "minimum_rows": minimum,
                "reason": (
                    "walk-forward requires complete point-in-time rows "
                    "and known net outcomes"
                ),
            }
        )
        return report

    result = evaluate_walk_forward(
        [row[2] for row in usable],
        [row[3] for row in usable],
        feature_names=names,
        train_size=train_size,
        validation_size=validation_size,
        holdout_size=holdout_size,
    )
    report.update({"status": "READY", "windows": list(result.windows)})
    return report
