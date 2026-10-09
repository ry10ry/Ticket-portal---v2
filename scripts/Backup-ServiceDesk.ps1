param(
    [Parameter(Mandatory=$true)][string]$BackupRoot,
    [string]$ProjectPath = (Split-Path -Parent $PSScriptRoot),
    [ValidateRange(1,104)][int]$Keep = 8
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$compose = @('compose', '-f', 'compose.yaml', '-f', 'compose.override.yaml', '-f', 'compose.ssl.yaml')
function Invoke-Docker([string[]]$Arguments) {
    $result = & docker @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed: $($result -join [Environment]::NewLine)" }
    return $result
}
$lock = $null
$resume = @()
$db = $null
$remoteDump = '/tmp/momcc-backup-' + [guid]::NewGuid().ToString('N') + '.sql'
$stage = $null
$failed = $null
Push-Location $ProjectPath
try {
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $BackupRoot = (Resolve-Path -LiteralPath $BackupRoot).Path
    # One backup at a time, including manual runs and scheduled tasks.
    $lock = [IO.File]::Open((Join-Path $BackupRoot '.backup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    Invoke-Docker -Arguments @('info', '--format', '{{.ServerVersion}}') | Out-Null
    $db = @(Invoke-Docker ($compose + @('ps', '-a', '-q', 'db')))[0]
    $web = @(Invoke-Docker ($compose + @('ps', '-a', '-q', 'web')))[0]
    if (-not $db -or -not $web) { throw 'Database or Web container not found.' }
    if ((Invoke-Docker -Arguments @('inspect', '--format', '{{.State.Running}}', $db)) -ne 'true') {
        throw 'Database container must be running.'
    }
    $name = 'MOMCC-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
    $stage = Join-Path $BackupRoot ($name + '.partial')
    New-Item -ItemType Directory -Path $stage | Out-Null
    # Stop only services that were running. Restore them even if backup fails.
    foreach ($service in @('web', 'scheduler')) {
        $ids = @(Invoke-Docker ($compose + @('ps', '-a', '-q', $service)))
        foreach ($id in $ids) {
            if ($id -and (Invoke-Docker -Arguments @('inspect', '--format', '{{.State.Running}}', $id)) -eq 'true') {
                $resume += $id
                Invoke-Docker -Arguments @('stop', '--time', '60', $id) | Out-Null
            }
        }
    }
    # Dump inside Linux, then docker cp: no PowerShell SQL encoding conversions.
    $dump = 'export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysqldump -uroot --single-transaction --quick --hex-blob --routines --triggers --events --no-tablespaces --databases "$MYSQL_DATABASE" > "$1"'
    $dump = $dump.Replace('$1', $remoteDump)
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($dump))
    Invoke-Docker -Arguments @('exec', $db, 'sh', '-c', ('echo ' + $encoded + ' | base64 -d | sh')) | Out-Null
    Invoke-Docker -Arguments @('cp', ($db + ':' + $remoteDump), (Join-Path $stage 'database.sql')) | Out-Null
    if ((Get-Item -LiteralPath (Join-Path $stage 'database.sql')).Length -eq 0) { throw 'Database dump is empty.' }
    New-Item -ItemType Directory -Path (Join-Path $stage 'attachments') | Out-Null
    Invoke-Docker -Arguments @('cp', ($web + ':/data/attachments/.'), (Join-Path $stage 'attachments')) | Out-Null
    $deployment = Join-Path $stage 'deployment'
    New-Item -ItemType Directory -Path $deployment | Out-Null
    foreach ($required in @('app', 'Dockerfile', 'requirements.txt', 'compose.yaml', 'compose.override.yaml', 'compose.ssl.yaml', '.env', 'ssl')) {
        $source = Join-Path $ProjectPath $required
        if (-not (Test-Path -LiteralPath $source)) { throw "Missing deployment item: $required" }
        Copy-Item -LiteralPath $source -Destination $deployment -Recurse -Force
    }
    if (Test-Path -LiteralPath (Join-Path $ProjectPath 'scripts')) {
        Copy-Item -LiteralPath (Join-Path $ProjectPath 'scripts') -Destination $deployment -Recurse -Force
    }
    $manifest = @(Get-ChildItem -LiteralPath $stage -File -Recurse | ForEach-Object {
        [pscustomobject]@{path=$_.FullName.Substring($stage.Length+1); bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
    })
    ConvertTo-Json -InputObject $manifest -Depth 4 | Set-Content -LiteralPath (Join-Path $stage 'manifest.json') -Encoding UTF8
} catch {
    $failed = $_
} finally {
    foreach ($id in $resume) {
        try { Invoke-Docker -Arguments @('start', $id) | Out-Null }
        catch { if (-not $failed) { $failed = $_ }; Write-Warning 'Could not restart a service; check Docker containers immediately.' }
    }
    if ($db) { try { Invoke-Docker -Arguments @('exec', $db, 'rm', '-f', $remoteDump) | Out-Null } catch { Write-Warning 'Temporary database dump cleanup failed.' } }
    Pop-Location
}
try {
    if ($failed) {
        if ($stage -and (Test-Path -LiteralPath $stage)) { 'Backup failed. This partial folder is not a completed backup.' | Set-Content (Join-Path $stage 'FAILED.txt') }
        throw $failed
    }
    @{completedAt=(Get-Date).ToString('o'); formatVersion=1} | ConvertTo-Json | Set-Content (Join-Path $stage 'SUCCESS.json') -Encoding UTF8
    $final = $stage.Substring(0, $stage.Length - '.partial'.Length)
    Move-Item -LiteralPath $stage -Destination $final
    # Prune only completed folders created by this script, after a successful run.
    Get-ChildItem -LiteralPath $BackupRoot -Directory | Where-Object {
        $_.Name -match '^MOMCC-\d{8}-\d{6}-[a-f0-9]{8}$' -and (Test-Path (Join-Path $_.FullName 'SUCCESS.json'))
    } | Sort-Object LastWriteTime,Name -Descending | Select-Object -Skip $Keep | Remove-Item -Recurse -Force
    Write-Output "Backup completed: $final"
} finally {
    if ($lock) { $lock.Dispose() }
}
