#!/bin/bash
# Проверка firewall и сегментации: пытается открыть TCP-соединения между контейнерами
# и сравнивает результат с ожидаемым. Запуск из папки проекта (в Windows — в Git Bash):
#   bash firewall/test.sh
export MSYS_NO_PATHCONV=1   # Git Bash на Windows не должен переписывать пути в аргументах
pass=0; fail=0

# Попытка соединения изнутри контейнера. Печатает: open | timeout | unreachable | refused
probe() {
  local from=$1 ip=$2 port=$3
  if [ "$from" = frontend ]; then
    docker compose exec -T frontend sh -c "nc -z -w 2 $ip $port >/dev/null 2>&1 && echo open || echo timeout"
  else
    docker compose exec -T "$from" python -c "
import errno, socket, sys
s = socket.socket(); s.settimeout(2)
try:
    s.connect(('$ip', $port)); print('open')
except socket.timeout: print('timeout')
except OSError as e:
    print({errno.ENETUNREACH: 'unreachable', errno.EHOSTUNREACH: 'unreachable', errno.ECONNREFUSED: 'refused'}.get(e.errno, 'timeout'))
"
  fi
}

check() {   # check <ожидание allow|deny> <откуда> <ip> <порт> <описание>
  local expect=$1 from=$2 ip=$3 port=$4 desc=$5 res verdict why
  res=$(probe "$from" "$ip" "$port" | tr -d '\r')
  case $res in
    open)        why="соединение установлено" ;;
    timeout)     why="пакеты отброшены firewall (DROP)" ;;
    unreachable) why="нет маршрута: другой сегмент сети" ;;
    refused)     why="порт закрыт" ;;
  esac
  if { [ "$expect" = allow ] && [ "$res" = open ]; } || { [ "$expect" = deny ] && [ "$res" != open ]; }; then
    verdict="OK  "; pass=$((pass+1))
  else
    verdict="FAIL"; fail=$((fail+1))
  fi
  printf "  [%s] %-6s %-13s -> %-11s %-6s %-44s %s\n" "$verdict" "$expect" "$from" "$ip" "$port" "$desc" "($why)"
}

echo "== Разрешённые соединения (должны проходить)"
check allow frontend     10.77.2.20 8000 "nginx -> auth-service"
check allow frontend     10.77.2.30 8000 "nginx -> api"
check allow api          10.77.2.20 8000 "api -> auth-service (ключ JWT)"
check allow auth-service 10.77.3.40 5432 "auth-service -> users-db"
check allow api          10.77.4.40 5432 "api -> video-db"
check allow video-worker 10.77.4.40 5432 "video-worker -> video-db"
check allow api          10.77.5.40 6379 "api -> redis"
check allow api          10.77.6.40 5672 "api -> rabbitmq AMQP"
check allow video-worker 10.77.6.40 5672 "video-worker -> rabbitmq AMQP"
check allow api          10.77.7.40 8333 "api -> S3"
check allow video-worker 10.77.7.40 8333 "video-worker -> S3"

echo
echo "== Запрещено firewall (один сегмент, но правила нет)"
check deny  auth-service 10.77.2.30 8000 "auth-service -> api"
check deny  api          10.77.2.10 80   "api -> frontend"
check deny  video-worker 10.77.4.30 8000 "video-worker -> api"
check deny  api          10.77.6.40 15672 "api -> консоль RabbitMQ"
check deny  video-worker 10.77.6.30 8000 "video-worker -> api (через broker)"
check deny  api          10.77.7.40 9333 "api -> SeaweedFS master"
check deny  video-worker 10.77.7.40 8888 "video-worker -> SeaweedFS filer"
check deny  frontend     1.1.1.1    443  "frontend -> интернет"

echo
echo "== Запрещено сегментацией (сервисы в разных сетях)"
check deny  api          10.77.3.40 5432 "api -> users-db (пароли)"
check deny  video-worker 10.77.5.40 6379 "video-worker -> redis"
check deny  auth-service 10.77.5.40 6379 "auth-service -> redis"
check deny  auth-service 10.77.4.40 5432 "auth-service -> video-db"
check deny  frontend     10.77.4.40 5432 "frontend -> video-db"
check deny  frontend     10.77.5.40 6379 "frontend -> redis"

echo
echo "Итого: пройдено $pass, ошибок $fail"
[ "$fail" -eq 0 ]
