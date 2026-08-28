#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv run ruff check src tests
uv run pytest --cov=day_trading_engine --cov-report=term-missing --cov-fail-under=90
