from math import inf
from pathlib import Path

import pytest

from day_trading_engine.core.config import AppConfig, load_config

ROOT = Path(__file__).resolve().parents[2]


def test_v1_config_loads_and_preserves_locked_contract() -> None:
    """The shipped configuration must preserve all locked V1 values."""
    config = load_config(ROOT / "configs" / "v1.yaml")
    assert config.validation.starting_cash_usd == 100.0
    assert config.validation.max_active_positions == 1
    assert config.research.daily_candidate_count == 30
    assert config.research.final_candidate_min == 2
    assert config.research.final_candidate_max == 5
    assert config.runtime.ai_required_for_daily_run is False


def test_v1_rejects_capital_change() -> None:
    """V1 must reject any change to the fixed starting capital."""
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["validation"]["starting_cash_usd"] = 101.0
    with pytest.raises(ValueError, match="exactly 100"):
        AppConfig.model_validate(config)


def test_v1_rejects_candidate_count_change() -> None:
    """V1 must keep the research cohort at exactly 30 candidates."""
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["research"]["daily_candidate_count"] = 29
    with pytest.raises(ValueError, match="exactly 30"):
        AppConfig.model_validate(config)


def test_v1_rejects_finalist_range_change() -> None:
    """V1 must keep the user-facing finalist range at two through five."""
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["research"]["final_candidate_min"] = 1
    with pytest.raises(ValueError, match="2-5"):
        AppConfig.model_validate(config)


def test_ai_cannot_be_mandatory() -> None:
    """V1 daily operation must not require AI availability."""
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["runtime"]["ai_required_for_daily_run"] = True
    with pytest.raises(ValueError, match="AI cannot be mandatory"):
        AppConfig.model_validate(config)


def test_market_watchlist_is_normalized_at_config_boundary() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    watchlist = list(config["market_data"]["watchlist"])
    watchlist[0] = " aapl "
    config["market_data"]["watchlist"] = watchlist

    validated = AppConfig.model_validate(config)

    assert validated.market_data.watchlist[0] == "AAPL"


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
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config[path[0]][path[1]] = value
    with pytest.raises(ValueError, match="finite_number"):
        AppConfig.model_validate(config)


@pytest.mark.parametrize(
    "invalid_time",
    ["9:00", "09:0", "24:00", "09:60", "0900", "0٩:00", "09:0٩"],
)
def test_decision_time_requires_strict_hhmm(invalid_time: str) -> None:
    """Decision time must use ASCII digits in strict 24-hour HH:MM format."""
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["project"]["decision_time"] = invalid_time
    with pytest.raises(ValueError, match="strict HH:MM"):
        AppConfig.model_validate(config)


@pytest.mark.parametrize("valid_time", ["00:00", "23:59"])
def test_decision_time_accepts_valid_boundaries(valid_time: str) -> None:
    """Decision time accepts the inclusive 24-hour clock boundaries."""
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["project"]["decision_time"] = valid_time
    assert AppConfig.model_validate(config).project.decision_time == valid_time


def test_unknown_timezone_is_normalized_to_value_error() -> None:
    """Unknown timezones must fail as normal validation errors."""
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["project"]["timezone"] = "Definitely/Not_A_Timezone"
    with pytest.raises(ValueError, match="unknown timezone"):
        AppConfig.model_validate(config)
