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

function Register-DailyEngineTask {
    param(
        [string]$Name,
        [string]$At,
        [string]$Arguments,
        [int]$MaxHours = 2
    )
    $action = New-ScheduledTaskAction -Execute $uv -WorkingDirectory $root -Argument $Arguments
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours $MaxHours)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
}

Register-DailyEngineTask "DayTradingEngine-DataQuality" "06:00" `
    "run python -m day_trading_engine.ops.scheduled quality"
Register-DailyEngineTask "DayTradingEngine-History" "06:15" `
    "run python -m day_trading_engine.ops.scheduled history" -MaxHours 4
Register-DailyEngineTask "DayTradingEngine-ScanDecision" "07:25" `
    "run python -m day_trading_engine.engine.live" -MaxHours 7
Register-DailyEngineTask "DayTradingEngine-AfterClose" "14:30" `
    "run python -m day_trading_engine.ops.scheduled after-close"
Register-DailyEngineTask "DayTradingEngine-Backup" "14:45" `
    "run python -m day_trading_engine.ops.scheduled backup `"$destination`"" -MaxHours 4
Register-DailyEngineTask "DayTradingEngine-MonthEndSnapshot" "15:00" `
    "run python -m day_trading_engine.ops.scheduled snapshot `"$destination`"" -MaxHours 4

Write-Host "Scheduled local day-trading workflow tasks."
