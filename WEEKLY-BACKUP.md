# Windows weekly backup

Copy the scripts folder into the deployed project. This does not install a task on your Windows computer automatically. Choose a destination on another disk or NAS; a folder on the same failed disk is not disaster recovery. Backup folders include database records, CR/SR files, Fault attachments, app code, Compose files, .env and SSL files. Treat them as confidential and restrict access.

Configured destination: C:\Docker databackup. This is on the Windows system drive; copy successful backups off the machine for disk-failure recovery. Install under the Windows user who runs Docker Desktop:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-WeeklyBackup.ps1 -BackupRoot "C:\Docker databackup"
Start-ScheduledTask -TaskName "MOMCC ServiceDesk Weekly Backup"
```

Defaults: Sunday 02:00 in Windows local time; retain 8 successful backups. Override with -Day Friday -Time "23:00" -Keep 12. Use an absolute UNC path for NAS; mapped drives are unreliable in scheduled tasks. The current user needs write permission at the destination. Configure Docker Desktop to start on login and Windows timezone to Singapore. The task runs only while that user is logged in (locking the screen is fine) and Docker Desktop is running. StartWhenAvailable catches missed runs and failures retry three times 15 minutes apart. A powered-off PC cannot back up.

Portal downtime: Web and Scheduler are stopped for consistent database/attachment snapshots and restarted in finally, including after errors. Existing stopped services are not started. Schedule when the portal is unused. External applications must not write directly to the database during backup. The database remains running. No production restore or volume deletion is performed.

Check scripts/backup-logs for output and Task Scheduler Last Run Result (0 means success). Every completed folder contains database.sql, attachments/, deployment/, manifest.json and SUCCESS.json. .partial folders are failures/incomplete backups and are not pruned as successful backups. Retention removes only this script's completed backup folders, after a new successful backup. Backups use directories rather than ZIP to avoid PowerShell 5 ZIP size limits. Do not interrupt Windows while services are paused for backup.

Validate hashes on a downloaded/restored backup:

```powershell
$backup = "C:\Docker databackup\MOMCC-YYYYMMDD-HHMMSS-xxxxxxxx"
Get-Content "$backup\manifest.json" -Raw | ConvertFrom-Json | ForEach-Object {
    if ((Get-FileHash -LiteralPath (Join-Path $backup $_.path) -Algorithm SHA256).Hash -ne $_.sha256) { throw "Hash mismatch: $($_.path)" }
}
```

Recovery onto a replacement Windows computer:

1. Install Docker Desktop and copy deployment/ from a successful backup into your project folder. Review .env and SSL files; preserve database credentials. Ensure this is a fresh recovery environment, not an existing live database.
2. Create the external volumes on the new machine: docker volume create momcc-servicedesk_mysql_data and docker volume create momcc-servicedesk_attachments_data. Do not create replacement empty volumes on the original server.
3. Using the same three -f Compose files, start only db: docker compose -f compose.yaml -f compose.override.yaml -f compose.ssl.yaml up -d db. Wait until healthy. Never start the app before importing the dump.
4. Copy database.sql into the db container using docker cp. Import inside the container with mysql, using MYSQL_PWD from MYSQL_ROOT_PASSWORD and stdin redirected from the copied file; do not pipe SQL through Windows PowerShell 5, which can alter encoding. Example (replace DB_CONTAINER_ID with the actual container ID):

```powershell
$dbId = docker compose -f compose.yaml -f compose.override.yaml -f compose.ssl.yaml ps -q db
docker cp "$backup\database.sql" "${dbId}:/tmp/restore.sql"
$restore = 'export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql -uroot < /tmp/restore.sql'
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($restore))
docker exec $dbId sh -c "echo $encoded | base64 -d | sh"
docker exec $dbId rm -f /tmp/restore.sql
```

5. Create (do not start) web with the three-file Compose command and create web; obtain its ID with ps -a -q web. Copy the contents of attachments/ using docker cp "$backup\attachments\." "${webId}:/data/attachments". Before running web, use docker compose run --rm --no-deps --user root --entrypoint sh web -c 'chown -R desk:desk /data/attachments' with the same three -f flags to restore write permissions.
6. Start the complete stack with up -d. Verify login, ticket counts, CR/SR histories and attachment downloads; reinstall the weekly task for the new machine and backup destination.

Always rehearse recovery in an isolated environment before relying on these backups. A Windows machine unable to boot is recoverable from a successful off-machine backup, subject to its last backup time.
