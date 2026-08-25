#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
python="$root/.venv/bin/python"
if [[ ! -x "$python" ]]; then
  echo "Project environment not found. Run 'uv sync --locked --dev' in $root first." >&2
  exit 2
fi
cd "$root"
exec "$python" -m day_trading_engine.market_data.capacity "$@"
