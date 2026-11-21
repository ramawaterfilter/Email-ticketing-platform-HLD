#!/usr/bin/env bash
# Nightly database backup for the support desk. Run from the project root.
# Schedule it with cron, e.g.:  0 2 * * *  /opt/support-desk/scripts/backup.sh >> /var/log/desk-backup.log 2>&1
#
# The desk stores no files of its own (attachments stay in Gmail), so the database is the
# only thing to back up. Copy BACKUP_DIR offsite (S3/GCS/rsync) - a backup on the same box
# does not survive losing the box.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

# pg_dump runs inside the db container; -Fc is the compressed custom format (restore with pg_restore).
docker compose exec -T db pg_dump -U desk -Fc desk > "$BACKUP_DIR/desk-$STAMP.dump"
echo "wrote $BACKUP_DIR/desk-$STAMP.dump"

# Rotate: drop dumps older than KEEP_DAYS.
find "$BACKUP_DIR" -name 'desk-*.dump' -type f -mtime "+$KEEP_DAYS" -delete

# Offsite copy - fill in for your provider, e.g.:
# aws s3 cp "$BACKUP_DIR/desk-$STAMP.dump" "s3://your-bucket/desk-backups/"
#
# Restore a dump into a fresh database:
#   docker compose exec -T db pg_restore -U desk -d desk --clean --if-exists < backups/desk-YYYYMMDD-HHMMSS.dump
