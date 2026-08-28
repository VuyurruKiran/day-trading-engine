from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from day_trading_engine.features.market import build_market_features


def test_rejects_implausible_intraday_price_scale_jump() -> None:
    start = datetime(2026, 8, 28, 13, 30, tzinfo=UTC)
    samples = pd.DataFrame(
        [
            {
                "received_at": start,
                "last_trade_price": 10.0,
                "volume": 100_000,
                "bid_price": 9.99,
                "ask_price": 10.01,
            },
            {
                "received_at": start + timedelta(minutes=1),
                "last_trade_price": 320.0,
                "volume": 110_000,
                "bid_price": 319.99,
                "ask_price": 320.01,
            },
        ]
    )

    with pytest.raises(ValueError, match="implausible price discontinuity"):
        build_market_features(samples, as_of=start + timedelta(minutes=1))
