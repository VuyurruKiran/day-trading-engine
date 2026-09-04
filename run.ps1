$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$lockDirectory = Join-Path $PSScriptRoot "data"
New-Item -ItemType Directory -Force -Path $lockDirectory | Out-Null
try {
    $instanceLock = [System.IO.File]::Open(
        (Join-Path $lockDirectory "engine-ui.lock"),
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
}
catch [System.IO.IOException] {
    Write-Host "Day Trading Engine and UI are already running."
    exit 0
}

$engine = $null
$ui = $null
$exitCode = 1
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project environment not found. Run 'uv sync --locked --dev' first."
}

function Stop-EngineProcessTree {
    param([int]$ProcessId)
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" | ForEach-Object {
        Stop-EngineProcessTree -ProcessId $_.ProcessId
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

try {
    $engine = Start-Process -FilePath $python -ArgumentList @(
        "-m", "day_trading_engine.engine.live", "--stop-after-extended-close"
    ) -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
    $ui = Start-Process -FilePath $python -ArgumentList @(
        "-m", "day_trading_engine.ui.server"
    ) -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru

    while (-not $engine.HasExited -and -not $ui.HasExited) {
        Start-Sleep -Seconds 1
        $engine.Refresh()
        $ui.Refresh()
    }

    if ($engine.HasExited) {
        $exitCode = $engine.ExitCode
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
    if ($null -ne $engine -and -not $engine.HasExited) {
        Stop-EngineProcessTree -ProcessId $engine.Id
    }
    if ($null -ne $ui -and -not $ui.HasExited) {
        Stop-EngineProcessTree -ProcessId $ui.Id
    }
    $instanceLock.Dispose()
}
exit $exitCode
