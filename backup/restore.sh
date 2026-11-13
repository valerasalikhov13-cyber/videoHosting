#!/bin/sh
# Восстановление базы из резервной копии.
#   docker compose exec backup sh /scripts/restore.sh                          # список копий
#   docker compose exec backup sh /scripts/restore.sh videos-20261113-120000.dump
#   docker compose exec backup sh /scripts/restore.sh latest                   # последняя копия videos
set -e
DIR=/backups
if [ -z "${1:-}" ]; then echo "Доступные копии:"; ls -1t "$DIR"; exit 0; fi
FILE=$1
[ "$FILE" = latest ] && FILE=$(ls -1t "$DIR"/videos-*.dump | head -1 | xargs basename)
[ -f "$DIR/$FILE" ] || { echo "Нет файла $DIR/$FILE"; exit 1; }

case $FILE in
  videos-*) export PGPASSWORD="$VIDEO_DB_PASSWORD"
            CONN="host=video-db,video-db-standby target_session_attrs=read-write dbname=$VIDEO_DB_NAME user=$VIDEO_DB_USER" ;;
  users-*)  export PGPASSWORD="$USERS_DB_PASSWORD"
            CONN="host=users-db dbname=$USERS_DB_NAME user=$USERS_DB_USER" ;;
  *) echo "Имя файла должно начинаться с videos- или users-"; exit 1 ;;
esac

echo "restore: восстанавливаю $FILE"
# Обрываем соединения приложений, чтобы снять блокировки таблиц
psql -d "$CONN" -qAt -c "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()" >/dev/null
pg_restore --clean --if-exists --no-owner --single-transaction -d "$CONN" "$DIR/$FILE"
echo "restore: готово. Строк в таблицах:"
case $FILE in
  videos-*) psql -d "$CONN" -c "SELECT (SELECT count(*) FROM channels) AS channels, (SELECT count(*) FROM videos) AS videos, (SELECT count(*) FROM votes) AS votes, (SELECT count(*) FROM comments) AS comments" ;;
  users-*)  psql -d "$CONN" -c "SELECT count(*) AS users FROM users" ;;
esac
