#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv run python -m day_trading_engine.market_data.collector "$@"
