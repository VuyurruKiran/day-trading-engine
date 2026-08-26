#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python="$root/.venv/bin/python"
if [[ ! -x "$python" ]]; then
  printf "Project environment not found. Run 'uv sync --locked --dev' in %s first.\n" "$root" >&2
  exit 1
fi

cd "$root"
exec "$python" -m day_trading_engine.ops.maintenance bootstrap-universe "$@"
