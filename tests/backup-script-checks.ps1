$ErrorActionPreference='Stop'
$root=Join-Path ([IO.Path]::GetTempPath()) ('momcc-backup-test-'+[guid]::NewGuid().ToString('N'))
$project=Join-Path $root 'project'
$backups=Join-Path $root 'backup with spaces'
New-Item -ItemType Directory -Path $project -Force | Out-Null
foreach($dir in @('app','ssl','scripts')){New-Item -ItemType Directory -Path (Join-Path $project $dir)|Out-Null;Set-Content (Join-Path $project "$dir/fixture.txt") 'fixture'}
foreach($file in @('Dockerfile','requirements.txt','compose.yaml','compose.override.yaml','compose.ssl.yaml','.env')){Set-Content (Join-Path $project $file) 'fixture'}
$global:states=@{db='true';web='true';scheduler='true'}
$global:failDump=$false
function global:docker {
 $global:LASTEXITCODE=0
 switch($args[0]) {
  'info' {'test';return}
  'compose' {$args[-1];return}
  'inspect' {$global:states[$args[-1]];return}
  'stop' {$global:states[$args[-1]]='false';return}
  'start' {$global:states[$args[-1]]='true';return}
  'exec' {
   if($args -contains 'rm'){return}
   if($global:failDump){$global:LASTEXITCODE=1;'simulated dump failure';return}
   $encoded=($args[-1] -split ' ')[1]
   $decoded=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
   if(-not $decoded.Contains('--hex-blob') -or -not $decoded.Contains('MYSQL_ROOT_PASSWORD')){throw 'Unsafe dump command'}
   return
  }
  'cp' {
   if($args[1] -like 'db:*'){[IO.File]::WriteAllBytes($args[2],[Text.Encoding]::UTF8.GetBytes('SQL 字符 and binary blob 0x00FF'));return}
   [IO.File]::WriteAllBytes((Join-Path $args[2] 'image.png'),[byte[]](0,1,2,255));return
  }
  default {throw "Unexpected Docker operation: $args"}
 }
}
$script=Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/Backup-ServiceDesk.ps1'
& $script -BackupRoot $backups -ProjectPath $project -Keep 1
& $script -BackupRoot $backups -ProjectPath $project -Keep 1
$success=@(Get-ChildItem $backups -Directory | Where-Object {Test-Path (Join-Path $_.FullName 'SUCCESS.json')})
if($success.Count -ne 1){throw 'Retention failed'}
$folder=$success[0].FullName
$manifest=Get-Content (Join-Path $folder 'manifest.json') -Raw|ConvertFrom-Json
foreach($item in $manifest){if((Get-FileHash -LiteralPath (Join-Path $folder $item.path)).Hash -ne $item.sha256){throw 'Manifest mismatch'}}
if(-not(Test-Path (Join-Path $folder 'deployment/.env'))){throw 'Deployment missing'}
$global:failDump=$true
$failed=$false
try{& $script -BackupRoot $backups -ProjectPath $project -Keep 1}catch{$failed=$true}
if(-not $failed -or $global:states.web -ne 'true' -or $global:states.scheduler -ne 'true'){throw 'Failure recovery failed'}
if(@(Get-ChildItem $backups -Directory | Where-Object {Test-Path (Join-Path $_.FullName 'SUCCESS.json')}).Count -ne 1){throw 'Failure deleted successful backup'}
$global:failDump=$false;$global:states.scheduler='false'
& $script -BackupRoot $backups -ProjectPath $project -Keep 1
if($global:states.scheduler -ne 'false'){throw 'Previously stopped scheduler was started'}
Write-Output 'PASS: spaces, database bytes, attachment copy, deployment, hashes, retention, failure restarts, preservation of stopped services.'
Remove-Item $root -Recurse -Force
