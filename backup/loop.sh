#!/bin/sh
# Плановое резервное копирование: первая копия через минуту после старта, дальше раз в $BACKUP_INTERVAL секунд.
echo "backup: плановые копии каждые ${BACKUP_INTERVAL:-3600} с, хранится ${BACKUP_KEEP:-24}"
sleep 60
while true; do
  sh /scripts/backup.sh || echo "$(date '+%F %T') backup: ОШИБКА резервного копирования"
  sleep "${BACKUP_INTERVAL:-3600}"
done
