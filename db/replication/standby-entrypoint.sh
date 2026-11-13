#!/bin/sh
# Запуск standby: при первом старте копирует данные с primary (pg_basebackup)
# и дальше получает изменения потоковой репликацией. Работает только на чтение,
# пока сторож failover не выполнит pg_promote().
set -e
PGDATA=/var/lib/postgresql/data
mkdir -p "$PGDATA" && chown postgres:postgres "$PGDATA" && chmod 700 "$PGDATA"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "standby: копирую данные с primary ($PRIMARY_HOST)..."
  until pg_isready -h "$PRIMARY_HOST" -q; do sleep 2; done
  export PGPASSWORD="$REPL_PASSWORD"
  su-exec postgres pg_basebackup -h "$PRIMARY_HOST" -U "$REPL_USER" -D "$PGDATA" -X stream -R -P
  echo "standby: копия готова, запускаю в режиме hot standby"
fi
exec su-exec postgres postgres -c hot_standby=on -c wal_level=replica -c max_wal_senders=5 -c wal_keep_size=256MB \
  -c hba_file=/etc/postgresql/pg_hba.conf
