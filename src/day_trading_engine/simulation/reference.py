from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from day_trading_engine.simulation.port import Bar, SimulationResult


class ReferenceSimulationEngine:
    """Minimal deterministic simulator used only to prove the M0 engine boundary."""

    def replay(self, symbol: str, bars: Sequence[Bar]) -> SimulationResult:
        if not symbol.strip():
            raise ValueError("symbol is required")
        if not bars:
            raise ValueError("at least one bar is required")

        ordered = sorted(bars, key=lambda bar: bar.ts)
        payload = [
            {
                "ts": bar.ts.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in ordered
        ]
        digest = hashlib.sha256(
            json.dumps({"symbol": symbol.upper(), "bars": payload}, sort_keys=True).encode()
        ).hexdigest()
        return SimulationResult(
            deterministic_id=digest,
            bars_processed=len(ordered),
            final_close=ordered[-1].close,
        )
