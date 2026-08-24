$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
uv run ruff check src tests
uv run pytest --cov=day_trading_engine --cov-report=term-missing
