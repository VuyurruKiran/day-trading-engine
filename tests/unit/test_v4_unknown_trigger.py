import pytest

from day_trading_engine.research.cycle import _outcome_return


def _outcome(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "status": "complete",
        "fidelity": "BAR_ONLY",
        "entry_triggered": True,
        "outcome": "target_before_stop",
        "shadow_return": 0.02,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "triggered,outcome",
    [
        (None, "no_trigger"),
        ("false", "no_trigger"),
        (False, "target_before_stop"),
    ],
)
def test_unknown_or_inconsistent_trigger_state_stays_unknown(
    triggered: object, outcome: str
) -> None:
    assert _outcome_return(_outcome(entry_triggered=triggered, outcome=outcome)) is None


def test_missing_trigger_state_stays_unknown() -> None:
    row = _outcome()
    row.pop("entry_triggered")
    assert _outcome_return(row) is None


def test_explicit_no_trigger_is_zero_return() -> None:
    assert _outcome_return(
        _outcome(entry_triggered=False, outcome="no_trigger", shadow_return=None)
    ) == 0.0
