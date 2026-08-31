$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

uv run ruff check src tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run pytest --cov=day_trading_engine --cov-report=term-missing --cov-fail-under=90
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
