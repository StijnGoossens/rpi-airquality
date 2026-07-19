#!/bin/sh
# Weekly DB backup: consistent SQLite snapshot -> dated copy in Google Drive.
set -e
DB="$HOME/Documents/rpi-airquality/airquality.db"
SNAP="/tmp/airquality-$(date +%Y-%m-%d).db"

# SQLite online backup API: safe to run while the monitor is writing.
python3 -c "import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()" "$DB" "$SNAP"

rclone copy "$SNAP" gdrive:rpi-airquality-backups
rm -f "$SNAP"
echo "$(date): backed up $(basename "$SNAP")"
