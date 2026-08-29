param(
  [ValidateSet("Install", "Uninstall")]
  [string]$Action = "Install",
  [string]$Python = "",
  [string]$Timezone = "Asia/Kolkata"
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Python) { $Python = Join-Path $Repo ".venv\Scripts\python.exe" }
$FridayTask = "TalkToMyPortfolio Weekly Sync"
$BackupTask = "TalkToMyPortfolio Weekly Sync Backup"

if ($Action -eq "Uninstall") {
  Unregister-ScheduledTask -TaskName $FridayTask -Confirm:$false -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $BackupTask -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "Removed weekly sync tasks."
  exit 0
}

if (-not (Test-Path $Python)) {
  throw "Python not found at $Python. Pass -Python with the project interpreter path."
}

$Command = "-m modules.portfolio.scripts.weekly_sync --mode auto"
$TaskAction = New-ScheduledTaskAction -Execute $Python -Argument $Command -WorkingDirectory $Repo
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$Friday = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Friday -At 6:30pm
$Saturday = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Saturday -At 9:00am

Register-ScheduledTask -TaskName $FridayTask -Action $TaskAction -Trigger $Friday -Settings $Settings -Force | Out-Null
Register-ScheduledTask -TaskName $BackupTask -Action $TaskAction -Trigger $Saturday -Settings $Settings -Force | Out-Null
Write-Host "Installed Friday 18:30 and Saturday 09:00 tasks in the Windows system timezone."
Write-Host "Set Windows to $Timezone (or convert the trigger times) for the documented schedule."
