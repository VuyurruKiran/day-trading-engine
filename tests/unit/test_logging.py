import json

from day_trading_engine.core.logging import configure_logging


def test_structured_logging_writes_json(tmp_path) -> None:
    path = tmp_path / "engine.log"
    logger = configure_logging(path)
    logger.info("health-check")
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["level"] == "INFO"
    assert payload["message"] == "health-check"
    assert payload["logger"] == "day_trading_engine"
    assert "timestamp" in payload
