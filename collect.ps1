$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
uv run python -m day_trading_engine.market_data.collector @args
