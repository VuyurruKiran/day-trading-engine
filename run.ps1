$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$engine = Start-Process -FilePath "uv" -ArgumentList @(
    "run", "python", "-m", "day_trading_engine.engine.live"
) -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
$ui = Start-Process -FilePath "uv" -ArgumentList @(
    "run", "python", "-m", "streamlit", "run", "src/day_trading_engine/ui/app.py"
) -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru

try {
    while (-not $engine.HasExited -and -not $ui.HasExited) {
        Start-Sleep -Seconds 1
        $engine.Refresh()
        $ui.Refresh()
    }

    if ($engine.HasExited) {
        $exitCode = $engine.ExitCode
        if ($exitCode -eq 0) { $exitCode = 1 }
    }
    else {
        $exitCode = $ui.ExitCode
    }
}
finally {
    if (-not $engine.HasExited) { Stop-Process -Id $engine.Id }
    if (-not $ui.HasExited) { Stop-Process -Id $ui.Id }
}
exit $exitCode
