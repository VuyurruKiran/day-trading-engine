$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project environment not found. Run 'uv sync --locked --dev' in $root first."
}
Push-Location $root
try {
    & $python -m day_trading_engine.ops.research --root $root @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
