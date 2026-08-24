from pathlib import Path

import pytest

from day_trading_engine.core.config import AppConfig, load_config

ROOT = Path(__file__).resolve().parents[2]


def test_v1_config_loads_and_preserves_locked_contract() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    assert config.validation.starting_cash_usd == 100.0
    assert config.validation.max_active_positions == 1
    assert config.research.daily_candidate_count == 30
    assert config.research.final_candidate_max == 5
    assert config.runtime.ai_required_for_daily_run is False


def test_v1_rejects_capital_change() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["validation"]["starting_cash_usd"] = 101.0
    with pytest.raises(ValueError, match="exactly 100"):
        AppConfig.model_validate(config)


def test_v1_rejects_candidate_count_change() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["research"]["daily_candidate_count"] = 29
    with pytest.raises(ValueError, match="exactly 30"):
        AppConfig.model_validate(config)


def test_ai_cannot_be_mandatory() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml").model_dump()
    config["runtime"]["ai_required_for_daily_run"] = True
    with pytest.raises(ValueError, match="AI cannot be mandatory"):
        AppConfig.model_validate(config)
