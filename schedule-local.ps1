param(
    [Parameter(Mandatory = $true)][string]$BackupDestination
)
$ErrorActionPreference = "Stop"
if ($BackupDestination.Contains('"') -or $BackupDestination.Contains("`r") -or `
    $BackupDestination.Contains("`n")) {
    throw "BackupDestination contains unsupported characters"
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$destination = [System.IO.Path]::GetFullPath($BackupDestination)
$uv = (Get-Command uv -ErrorAction Stop).Source
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source

function Register-DailyEngineTask {
    param(
        [string]$Name,
        [string]$At,
        [string]$Arguments,
        [string]$Execute = $uv,
        [int]$MaxHours = 2
    )
    $action = New-ScheduledTaskAction -Execute $Execute -WorkingDirectory $root `
        -Argument $Arguments
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours $MaxHours) -WakeToRun `
        -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
}

Register-DailyEngineTask "DayTradingEngine-DataQuality" "06:00" `
    "run python -m day_trading_engine.ops.scheduled quality"
Register-DailyEngineTask "DayTradingEngine-History" "06:15" `
    "run python -m day_trading_engine.ops.scheduled history" -MaxHours 4
Register-DailyEngineTask "DayTradingEngine-ScanDecision" "06:00" `
    "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\run.ps1`" -StopAfterExtendedClose" `
    -Execute $powershell -MaxHours 13
Register-DailyEngineTask "DayTradingEngine-AfterClose" "18:25" `
    "run python -m day_trading_engine.ops.scheduled after-close --destination `"$destination`"" -MaxHours 8
# Reporting, backup, and snapshot now run only after successful after-close completion.
foreach ($name in @("MonthlyReport", "Backup", "MonthEndSnapshot")) {
    Get-ScheduledTask -TaskName "DayTradingEngine-$name" -ErrorAction SilentlyContinue |
        Unregister-ScheduledTask -Confirm:$false
}

Write-Host "Scheduled local day-trading workflow tasks."
