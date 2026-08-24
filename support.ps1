$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $PSScriptRoot "support-$stamp.zip"
$temp = Join-Path $env:TEMP "day-trading-engine-support-$stamp"
New-Item -ItemType Directory -Force -Path $temp | Out-Null
Copy-Item "configs\v1.yaml" $temp
& uv run python -m day_trading_engine.doctor | Out-File (Join-Path $temp "doctor.txt") -Encoding utf8
if (Test-Path "logs") { Copy-Item "logs" (Join-Path $temp "logs") -Recurse -Force }
Compress-Archive -Path "$temp\*" -DestinationPath $out -Force
Remove-Item $temp -Recurse -Force
Write-Host "Support bundle: $out"
