# Protect the existing database volume

The deployment database volume was confirmed by docker inspect as `momcc-servicedesk_mysql_data`. The root compose.yaml now marks this existing volume external, so Docker Compose down -v does not remove it. Container mounts remain mysql_data:/var/lib/mysql; no data migration or volume creation is needed.

For an installation with local Compose edits, change only the bottom-level volume declaration:

```yaml
volumes:
  mysql_data:
    external: true
    name: momcc-servicedesk_mysql_data
```

Verify that the volume exists:

```powershell
docker volume inspect momcc-servicedesk_mysql_data
```

Then apply without stopping or deleting volumes:

```powershell
docker compose -f compose.yaml -f compose.override.yaml -f compose.ssl.yaml config --quiet
docker compose -f compose.yaml -f compose.override.yaml -f compose.ssl.yaml up -d
```

Do not create a new empty volume if inspect reports the volume missing; check the Docker context and actual deployment volume name first. All subsequent Compose commands must use the edited compose.yaml. This protects only the database volume. Fault Ticket attachments remain in the separately managed attachments_data volume until its actual mount name is confirmed and its declaration in compose.override.yaml is also marked external.

To inspect the actual attachment mount:

```powershell
docker inspect (docker compose -f compose.yaml -f compose.override.yaml -f compose.ssl.yaml ps -q web) --format '{{json .Mounts}}'
```

After confirming the Name of the mount at /data/attachments, add external: true and name: <that exact Name> under attachments_data in compose.override.yaml. External volumes can still be deleted manually; retain database dumps and attachment backups.
