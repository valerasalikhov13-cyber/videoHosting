#!/bin/sh
# Резервная копия обеих баз в формате pg_dump custom (-Fc). Хранятся последние $BACKUP_KEEP копий.
#   docker compose exec backup sh /scripts/backup.sh
set -e
DIR=/backups
KEEP=${BACKUP_KEEP:-24}
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p "$DIR"

# video-db: подключаемся к тому узлу, который сейчас master
PGPASSWORD="$VIDEO_DB_PASSWORD" pg_dump -Fc \
  -d "host=video-db,video-db-standby target_session_attrs=read-write dbname=$VIDEO_DB_NAME user=$VIDEO_DB_USER" \
  -f "$DIR/videos-$TS.dump"
PGPASSWORD="$USERS_DB_PASSWORD" pg_dump -Fc -h users-db -U "$USERS_DB_USER" -d "$USERS_DB_NAME" \
  -f "$DIR/users-$TS.dump"

echo "$(date '+%F %T') backup: готово videos-$TS.dump ($(du -h "$DIR/videos-$TS.dump" | cut -f1)), users-$TS.dump ($(du -h "$DIR/users-$TS.dump" | cut -f1))"

# Ротация: оставляем только последние KEEP копий каждой базы
for db in videos users; do
  ls -1t "$DIR"/$db-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
done
