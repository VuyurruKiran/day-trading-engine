$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv is required. Install uv, then rerun setup.ps1." }
uv sync --locked --dev
Write-Host "Setup complete. Run .\doctor.ps1 next."
