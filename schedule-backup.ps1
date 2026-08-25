param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$Time = "18:30"
)
$ErrorActionPreference = "Stop"
if ($Time -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw "Time must use HH:MM" }
if ($Destination.Contains('"') -or $Destination.Contains("`r") -or $Destination.Contains("`n")) {
    throw "Destination contains unsupported characters"
}
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backup = Join-Path $root "backup.ps1"
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root -Argument (
    "-NoProfile -ExecutionPolicy Bypass -File `"$backup`" `"$destinationPath`""
)
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
Register-ScheduledTask -TaskName "DayTradingEngineResearchBackup" -Action $action `
    -Trigger $trigger -Description "Daily day-trading research-data backup" -Force | Out-Null
Write-Host "Scheduled daily backup at $Time"
