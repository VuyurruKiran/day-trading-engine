$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$engine = $null
$ui = $null
$exitCode = 1

try {
    $engine = Start-Process -FilePath "uv" -ArgumentList @(
        "run", "python", "-m", "day_trading_engine.engine.live"
    ) -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
    $ui = Start-Process -FilePath "uv" -ArgumentList @(
        "run", "python", "-m", "day_trading_engine.ui.server"
    ) -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru

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
catch {
    Write-Error $_
    $exitCode = 1
}
finally {
    if ($null -ne $engine -and -not $engine.HasExited) { Stop-Process -Id $engine.Id }
    if ($null -ne $ui -and -not $ui.HasExited) { Stop-Process -Id $ui.Id }
}
exit $exitCode
