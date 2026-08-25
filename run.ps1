$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

uv run python -m streamlit run src/day_trading_engine/ui/app.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
