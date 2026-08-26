$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$engine = Start-Process -FilePath "uv" -ArgumentList @(
    "run", "python", "-m", "day_trading_engine.engine.live"
) -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
try {
    uv run python -m streamlit run src/day_trading_engine/ui/app.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    if (-not $engine.HasExited) {
        Stop-Process -Id $engine.Id
    }
}
