#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv run python -m day_trading_engine.engine.live &
engine_pid=$!
trap 'kill "$engine_pid" 2>/dev/null || true' EXIT
uv run python -m streamlit run src/day_trading_engine/ui/app.py
