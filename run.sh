#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv run streamlit run src/day_trading_engine/ui/app.py
