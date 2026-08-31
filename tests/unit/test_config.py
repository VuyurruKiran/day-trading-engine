from math import inf
from pathlib import Path

import pytest

from day_trading_engine.core.config import AppConfig, load_config

ROOT = Path(__file__).resolve().parents[2]


def _config() -> dict:
    return load_config(ROOT / "configs" / "v1.yaml").model_dump()


def test_v1_config_loads_and_preserves_locked_contract() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    assert config.project.plan_version == "3.1"
    assert config.validation.starting_cash_usd == 100.0
    assert config.validation.max_active_positions == 1
    assert config.research.daily_candidate_count == 30
    assert config.research.final_candidate_min == 1
    assert config.research.final_candidate_max == 5
    assert config.research_universe.target == 200
    assert not set(config.market_data.watchlist) & set(config.research_universe.benchmark_symbols)
    assert config.runtime.ai_required_for_daily_run is False


def test_v1_rejects_capital_change() -> None:
    config = _config()
    config["validation"]["starting_cash_usd"] = 101.0
    with pytest.raises(ValueError, match="exactly 100"):
        AppConfig.model_validate(config)


def test_v1_rejects_candidate_count_change() -> None:
    config = _config()
    config["research"]["daily_candidate_count"] = 29
    with pytest.raises(ValueError, match="exactly 30"):
        AppConfig.model_validate(config)


def test_v1_rejects_finalist_range_change() -> None:
    config = _config()
    config["research"]["final_candidate_min"] = 2
    with pytest.raises(ValueError, match="1-5"):
        AppConfig.model_validate(config)


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"core_candidate_count": 19}, "bucket counts"),
        ({"final_candidate_min": 5, "final_candidate_max": 4}, "cannot exceed"),
        ({"primary_candidate_max": 2}, "at most one PRIMARY"),
        (
            {
                "historical_bootstrap_months_min": 24,
                "historical_bootstrap_months_preferred": 12,
            },
            "preferred historical window",
        ),
    ],
)
def test_research_contract_rejects_internally_invalid_combinations(changes, message) -> None:
    config = _config()
    config["research"].update(changes)
    with pytest.raises(ValueError, match=message):
        AppConfig.model_validate(config)


def test_benchmarks_cannot_enter_research_watchlist() -> None:
    config = _config()
    watchlist = list(config["market_data"]["watchlist"])
    watchlist[0] = "SPY"
    config["market_data"]["watchlist"] = watchlist
    with pytest.raises(ValueError, match="benchmark symbols"):
        AppConfig.model_validate(config)


def test_ai_cannot_be_mandatory() -> None:
    config = _config()
    config["runtime"]["ai_required_for_daily_run"] = True
    with pytest.raises(ValueError, match="AI cannot be mandatory"):
        AppConfig.model_validate(config)


def test_market_watchlist_is_normalized_at_config_boundary() -> None:
    config = _config()
    watchlist = list(config["market_data"]["watchlist"])
    watchlist[0] = " aapl "
    config["market_data"]["watchlist"] = watchlist
    assert AppConfig.model_validate(config).market_data.watchlist[0] == "AAPL"


@pytest.mark.parametrize(
    "path,value",
    [
        (("risk", "max_spread_pct"), inf),
        (("risk", "max_volatility"), inf),
        (("strategy", "reward_to_risk"), inf),
    ],
)
def test_non_finite_risk_and_strategy_values_are_rejected(
    path: tuple[str, str], value: float
) -> None:
    config = _config()
    config[path[0]][path[1]] = value
    with pytest.raises(ValueError, match="finite_number"):
        AppConfig.model_validate(config)


@pytest.mark.parametrize(
    "invalid_time",
    ["9:00", "09:0", "24:00", "09:60", "0900", "0٩:00", "09:0٩"],
)
def test_decision_time_requires_strict_hhmm(invalid_time: str) -> None:
    config = _config()
    config["project"]["decision_time"] = invalid_time
    with pytest.raises(ValueError, match="strict HH:MM"):
        AppConfig.model_validate(config)


@pytest.mark.parametrize("valid_time", ["00:00", "23:59"])
def test_decision_time_accepts_valid_boundaries(valid_time: str) -> None:
    config = _config()
    config["project"]["decision_time"] = valid_time
    assert AppConfig.model_validate(config).project.decision_time == valid_time


def test_unknown_timezone_is_normalized_to_value_error() -> None:
    config = _config()
    config["project"]["timezone"] = "Definitely/Not_A_Timezone"
    with pytest.raises(ValueError, match="unknown timezone"):
        AppConfig.model_validate(config)


@pytest.mark.parametrize(
    "section,key,value,message",
    [
        ("research_universe", "refresh", "daily", "refresh must be monthly"),
        ("research_universe", "selector_version", " ", "selector_version is required"),
        ("research_universe", "benchmark_symbols", [], "benchmark_symbols must be non-empty"),
        (
            "research_universe",
            "benchmark_symbols",
            ["spy", "SPY"],
            "cannot contain duplicates",
        ),
        ("history", "provider", "other", "historical provider must be alpaca"),
        ("history", "interval", "5m", "bootstrap interval must be 1m"),
        ("ranking", "missing_optional_weight_to", "market", "must move to technical"),
        ("ranking", "normalization_version", " ", "normalization_version is required"),
        ("market_data", "provider", "other", "provider must be questrade"),
        ("strategy", "family", "other", "strategy family must be"),
    ],
)
def test_v31_subcontracts_fail_closed(section, key, value, message) -> None:
    config = _config()
    config[section][key] = value
    with pytest.raises(ValueError, match=message):
        AppConfig.model_validate(config)


def test_v31_rejects_history_window_inversion() -> None:
    config = _config()
    config["history"]["minimum_months"] = 24
    config["history"]["preferred_months"] = 12
    with pytest.raises(ValueError, match="preferred historical window"):
        AppConfig.model_validate(config)


def test_v31_rejects_ranking_weights_that_do_not_sum_to_one() -> None:
    config = _config()
    config["ranking"]["technical"] = 0.40
    with pytest.raises(ValueError, match="weights must sum to 1"):
        AppConfig.model_validate(config)


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda c: c["project"].update(plan_version="3.0"), "implementation plan 3.1"),
        (lambda c: c["validation"].update(allow_capital_top_up=True), "top-ups are forbidden"),
        (lambda c: c["validation"].update(long_only=False), "long-only"),
        (lambda c: c["validation"].update(leverage_allowed=True), "leverage is forbidden"),
        (lambda c: c["validation"].update(max_active_positions=2), "exactly one active position"),
        (
            lambda c: c["research"].update(core_candidate_count=19, boundary_candidate_count=6),
            "frozen 20/5/5",
        ),
        (lambda c: c["research"].update(primary_candidate_max=0), "at most 1 PRIMARY"),
        (lambda c: c["research_universe"].update(target=201), "target must remain 200"),
        (lambda c: c["history"].update(minimum_months=11), "12-month minimum"),
        (
            lambda c: c["ranking"].update(technical=0.49, market=0.21),
            "weights must remain 50/20/20/5/5",
        ),
        (lambda c: c["runtime"].update(ui="other"), "UI must remain custom-local"),
    ],
)
def test_v31_frozen_app_contract_rejects_valid_but_unsupported_changes(mutate, message) -> None:
    config = _config()
    mutate(config)
    with pytest.raises(ValueError, match=message):
        AppConfig.model_validate(config)
