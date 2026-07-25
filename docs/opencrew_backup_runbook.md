# OpenCrew Backup Runbook

This document records the local backup convention for this OpenCrew checkout and the latest verified manual backup.

## Current Local Backup

- Backup root: `/Users/macmini-1/.opencrew/backups/manual_20260610_114544`
- Created at: `2026-06-10T01:47:55Z`
- Database dump: `opencrew.dump`
- Schema dump: `schema.sql`
- Dump listing: `opencrew.dump.list`
- File archive: `opencrew_files_appdata.tgz`
- Checksums: `SHA256SUMS`
- Manifest: `manifest.txt`

The file archive covers `.opencrew` application data and excludes `backups/`, `postgres/`, and `runtimes/`. The PostgreSQL database is backed up separately with `pg_dump -Fc`.

## Verified Contents

```text
opencrew.dump                 4.1 MB
schema.sql                    44 KB
opencrew.dump.list            15 KB
opencrew_files_appdata.tgz    3.8 GB
```

Verification performed after creation:

```bash
cd /Users/macmini-1/.opencrew/backups/manual_20260610_114544
shasum -a 256 -c SHA256SUMS
pg_restore -l opencrew.dump
tar -tzf opencrew_files_appdata.tgz >/dev/null
scripts/opencrew_local_stack.sh status
curl -fsS http://127.0.0.1:8011/api/health
curl -fsS http://127.0.0.1:18080/api/auth/status
```

All checksum entries passed. `pg_restore -l` and `tar -tzf` completed successfully. The local backend and frontend were restarted and healthy afterward.

## Backup Procedure

Use a persistent location under `/Users/macmini-1/.opencrew/backups/`. Avoid `/private/tmp` for long-lived backups.

1. Create a timestamped backup directory:

   ```bash
   TS="$(date +%Y%m%d_%H%M%S)"
   BACKUP_ROOT="/Users/macmini-1/.opencrew/backups/manual_${TS}"
   mkdir -p "$BACKUP_ROOT"
   ```

2. Dump PostgreSQL:

   ```bash
   export PGPASSWORD=opencrew
   pg_dump -h 127.0.0.1 -p 5433 -U opencrew -d opencrew -Fc -f "$BACKUP_ROOT/opencrew.dump"
   pg_dump -h 127.0.0.1 -p 5433 -U opencrew -d opencrew --schema-only -f "$BACKUP_ROOT/schema.sql"
   pg_restore -l "$BACKUP_ROOT/opencrew.dump" > "$BACKUP_ROOT/opencrew.dump.list"
   ```

3. Stop the local frontend/backend while archiving files:

   ```bash
   scripts/opencrew_local_stack.sh stop
   tar -C /Users/macmini-1/.opencrew \
     --exclude './backups' \
     --exclude './postgres' \
     --exclude './runtimes' \
     -czf "$BACKUP_ROOT/opencrew_files_appdata.tgz" .
   scripts/opencrew_local_stack.sh start
   ```

4. Record checksums and a manifest:

   ```bash
   shasum -a 256 "$BACKUP_ROOT/opencrew.dump" "$BACKUP_ROOT/schema.sql" "$BACKUP_ROOT/opencrew_files_appdata.tgz" > "$BACKUP_ROOT/SHA256SUMS"
   printf 'backup_root=%s\ncreated_at=%s\ndatabase_dump=opencrew.dump\nschema=schema.sql\nfiles_archive=opencrew_files_appdata.tgz\nfiles_archive_scope=.opencrew excluding backups postgres runtimes\n' \
     "$BACKUP_ROOT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$BACKUP_ROOT/manifest.txt"
   ```

5. Verify the backup:

   ```bash
   cd "$BACKUP_ROOT"
   shasum -a 256 -c SHA256SUMS
   pg_restore -l opencrew.dump >/dev/null
   tar -tzf opencrew_files_appdata.tgz >/dev/null
   scripts/opencrew_local_stack.sh status
   ```

## Restore Notes

- Restore database from `opencrew.dump` with `pg_restore` into a clean or intentionally replaced `opencrew` database.
- Restore files by extracting `opencrew_files_appdata.tgz` into `/Users/macmini-1/.opencrew` after stopping the local frontend/backend.
- The archive includes local secret files such as `secrets.enc` and `secret_store.key`; keep backup directories private and do not commit or upload them.
- The archive does not include `runtimes/`; those are rebuildable runtime dependencies.
- The archive does not include `postgres/`; use the logical dump for database restore.
