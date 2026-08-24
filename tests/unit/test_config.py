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


@pytest.mark.parametrize("invalid_time", ["9:00", "09:0", "24:00", "09:60", "0900"])
def test_decision_time_requires_strict_hhmm(invalid_time: str) -> None:
    """Decision time must use strict 24-hour HH:MM formatting."""
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["project"]["decision_time"] = invalid_time
    with pytest.raises(ValueError, match="strict HH:MM"):
        AppConfig.model_validate(config)
