#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "uv is required" >&2; exit 1; }
uv sync --locked --dev
echo "Setup complete. Run ./doctor.sh next."
