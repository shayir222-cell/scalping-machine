#!/usr/bin/env bash
# Daily SQLite backup. Cron suggestion:
#   0 3 * * *  /home/app/scalping-machine/scripts/backup_db.sh
#
# Keeps 30 days of snapshots in ~/backups/.
set -euo pipefail

DB="/home/app/scalping-machine/scalping.db"
DIR="/home/app/backups"
STAMP=$(date -u +"%Y-%m-%d")
TARGET="${DIR}/scalping-${STAMP}.db"
RETAIN_DAYS=30

mkdir -p "$DIR"

# Live-backup via sqlite3 .backup (atomic, doesn't lock the bot)
sqlite3 "$DB" ".backup '$TARGET'"

# gzip to save space
gzip -f "$TARGET"

# Retention: delete anything older than RETAIN_DAYS
find "$DIR" -name "scalping-*.db.gz" -mtime "+$RETAIN_DAYS" -delete

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] backup OK → ${TARGET}.gz"
