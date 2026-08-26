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

while kill -0 "$engine_pid" 2>/dev/null && kill -0 "$ui_pid" 2>/dev/null; do
  sleep 1
done

set +e
if ! kill -0 "$engine_pid" 2>/dev/null; then
  wait "$engine_pid"
  status=$?
  if [ "$status" -eq 0 ]; then status=1; fi
else
  wait "$ui_pid"
  status=$?
fi
set -e
exit "$status"
