#!/usr/bin/env bash
# Nightly SQLite backup. Uses `.backup` (not cp) so it's safe while WAL is active
# and the services are writing. Keeps 14 days. Wire via cron (see DEPLOY.txt).
set -euo pipefail

DB="${BANKS_DB_PATH:-/root/banks/banks.db}"
DEST="/root/backups"
mkdir -p "$DEST"

stamp="$(date +%F_%H%M)"
sqlite3 "$DB" ".backup '$DEST/banks-$stamp.db'"

# prune backups older than 14 days
find "$DEST" -name 'banks-*.db' -mtime +14 -delete

echo "backup ok -> $DEST/banks-$stamp.db"
