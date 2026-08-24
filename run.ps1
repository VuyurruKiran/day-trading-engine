$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
uv run streamlit run src/day_trading_engine/ui/app.py
