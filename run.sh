#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
cd "$root"
python="$root/.venv/bin/python"
if [[ ! -x $python ]]; then
  echo "Project environment not found. Run 'uv sync --locked --dev' first." >&2
  exit 2
fi

"$python" -m day_trading_engine.engine.live --stop-after-extended-close &
engine_pid=$!
"$python" -m day_trading_engine.ui.server &
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
else
  wait "$ui_pid"
  status=$?
fi
set -e
exit "$status"
