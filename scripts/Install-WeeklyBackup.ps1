param(
    [string]$BackupRoot = 'C:\Docker databackup',
    [ValidateSet('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')][string]$Day = 'Sunday',
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')][string]$Time = '02:00',
    [ValidateRange(1,104)][int]$Keep = 8,
    [string]$ProjectPath = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
$ProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
if ($BackupRoot.Contains('"') -or $ProjectPath.Contains('"') -or -not [IO.Path]::IsPathRooted($BackupRoot)) {
    throw 'Use an absolute backup path without quotation marks in the path itself.'
}
$runner = Join-Path $PSScriptRoot 'Run-WeeklyBackup.ps1'
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -BackupRoot "{1}" -ProjectPath "{2}" -Keep {3}' -f $runner,$BackupRoot,$ProjectPath,$Keep
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments -WorkingDirectory $ProjectPath
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $Day -At $Time
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
# Docker Desktop belongs to the logged-in user's session; SYSTEM cannot use it reliably.
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'MOMCC ServiceDesk Weekly Backup' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Weekly database, attachment and deployment backup. Docker Desktop and logged-in user required.' -Force | Out-Null
Write-Output "Installed: $Day at $Time (Windows local time), retaining $Keep successful backups."
Write-Output "Destination: $BackupRoot"
Write-Output 'Run the task once and check the log and SUCCESS.json before relying on the schedule.'
