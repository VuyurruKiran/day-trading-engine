#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

uv run python -m day_trading_engine.engine.live &
engine_pid=$!
uv run python -m streamlit run src/day_trading_engine/ui/app.py &
ui_pid=$!

cleanup() {
  kill "$engine_pid" "$ui_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

set +e
wait -n "$engine_pid" "$ui_pid"
status=$?
set -e
exit "$status"
