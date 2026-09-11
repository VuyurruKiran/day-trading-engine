import pytest

from day_trading_engine.research.model import (
    LogisticBaseline,
    binary_metrics,
    build_model_evidence,
    evaluate_walk_forward,
    walk_forward_splits,
)


def test_model_evidence_preserves_missing_rows_and_reports_insufficient_data() -> None:
    report = build_model_evidence(
        [
            {
                "session": "2026-08-28",
                "snapshot_id": "s",
                "symbol": "A",
                "eligible": True,
                "features": {},
            },
            {
                "session": "2026-08-28",
                "snapshot_id": "s",
                "symbol": "B",
                "eligible": False,
                "features": {},
            },
        ],
        [],
    )
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["excluded"] == {
        "ineligible": 1,
        "missing_features": 1,
        "unknown_outcome": 0,
    }


def test_model_evidence_runs_walk_forward_on_known_net_outcomes() -> None:
    candidates = []
    outcomes = []
    for index in range(6):
        candidates.append(
            {
                "session": f"2026-08-{index + 1:02d}",
                "snapshot_id": f"s{index}",
                "symbol": "A",
                "eligible": True,
                "features": {"signal": float(index)},
            }
        )
        outcomes.append(
            {
                "snapshot_id": f"s{index}",
                "symbol": "A",
                "net_return": 0.1 if index >= 3 else -0.1,
            }
        )
    report = build_model_evidence(
        candidates,
        outcomes,
        feature_names=("signal",),
        train_size=2,
        validation_size=2,
        holdout_size=2,
    )
    assert report["status"] == "READY"
    assert len(report["windows"]) == 1


def test_logistic_baseline_is_deterministic_and_interpretable() -> None:
    rows = [{"momentum": value, "liquidity": 1.0} for value in (-2, -1, 1, 2)]
    model = LogisticBaseline.fit(rows, [0, 0, 1, 1], feature_names=("momentum", "liquidity"))

    assert model.predict_proba(rows[0]) < model.predict_proba(rows[-1])
    assert set(model.feature_importance()) == {"momentum", "liquidity"}


def test_model_evidence_counts_invalid_outcome_and_nonfinite_features() -> None:
    report = build_model_evidence(
        [
            {
                "session": "2026-08-28",
                "snapshot_id": "s1",
                "symbol": "A",
                "eligible": True,
                "features": {"signal": float("nan")},
            },
            {
                "session": "2026-08-29",
                "snapshot_id": "s2",
                "symbol": "A",
                "eligible": True,
                "features": {"signal": 1.0},
            },
        ],
        [{"snapshot_id": "s2", "symbol": "A", "net_return": "unknown"}],
        feature_names=("signal",),
    )

    assert report["excluded"] == {
        "ineligible": 0,
        "missing_features": 1,
        "unknown_outcome": 1,
    }


def test_walk_forward_windows_do_not_leak() -> None:
    windows = walk_forward_splits(10, train_size=4, validation_size=2, holdout_size=2)

    assert windows[0] == (range(0, 4), range(4, 6), range(6, 8))
    assert max(windows[0][0]) < min(windows[0][1]) < min(windows[0][2])


def test_binary_metrics_validate_and_report_calibration() -> None:
    metrics = binary_metrics((0.1, 0.9), (0, 1))

    assert metrics["accuracy"] == 1.0
    assert metrics["brier"] == pytest.approx(0.01)


def test_walk_forward_evaluation_scores_future_windows_separately() -> None:
    rows = [{"momentum": value} for value in range(-6, 6)]
    labels = [value > 0 for value in range(-6, 6)]

    result = evaluate_walk_forward(
        rows,
        labels,
        feature_names=("momentum",),
        train_size=6,
        validation_size=2,
        holdout_size=2,
    )

    assert len(result.windows) == 2
    assert 0.0 <= result.windows[0]["holdout"]["accuracy"] <= 1.0
