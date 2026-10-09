param(
    [Parameter(Mandatory=$true)][string]$BackupRoot,
    [Parameter(Mandatory=$true)][string]$ProjectPath,
    [int]$Keep = 8
)
$ErrorActionPreference = 'Stop'
$logs = Join-Path $PSScriptRoot 'backup-logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$log = Join-Path $logs ((Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
try {
    & (Join-Path $PSScriptRoot 'Backup-ServiceDesk.ps1') -BackupRoot $BackupRoot -ProjectPath $ProjectPath -Keep $Keep *>> $log
    exit 0
} catch {
    ('FAILED: ' + $_.Exception.Message) | Out-File -LiteralPath $log -Append
    exit 1
}
