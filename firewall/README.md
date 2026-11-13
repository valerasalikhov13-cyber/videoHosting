# Этап 4. Сегментация сети и firewall

Защита устроена в два слоя:

1. **Сегментация.** Сервисы разнесены по восьми Docker-сетям. Каждый контейнер подключён только
   к тем сетям, где лежат нужные ему компоненты. Между разными сетями маршрута нет.
   Шесть сетей помечены `internal: true` и не имеют выхода наружу.
2. **Firewall.** Контейнер `firewall` загружает правила iptables в цепочку `DOCKER-USER` хоста
   Docker. Разрешены только перечисленные пары «источник → назначение:порт», всё остальное
   отбрасывается (DROP) и учитывается счётчиком. Так закрыты лишние соединения даже внутри
   одного сегмента: например, api и video-worker сидят в одной сети `broker`, но друг к другу
   подключиться не могут, а api не достаёт до веб-консоли RabbitMQ.

## Сегменты

| Сеть | Подсеть | Участники | Наружу | Зачем |
|---|---|---|---|---|
| `edge` | 10.77.1.0/24 | frontend (.10) | да, порт 8080 | вход пользователя |
| `app` | 10.77.2.0/24 | frontend (.10), auth-service (.20), api (.30) | нет | nginx → сервисы, api → ключ JWT |
| `users-data` | 10.77.3.0/24 | auth-service (.20), users-db (.40) | нет | пароли видит только auth-service |
| `video-data` | 10.77.4.0/24 | api (.30), video-worker (.50), video-db (.40) | нет | метаданные видео |
| `cache` | 10.77.5.0/24 | api (.30), redis (.40) | нет | кеш |
| `broker` | 10.77.6.0/24 | api (.30), video-worker (.50), rabbitmq (.40) | нет | очередь задач |
| `storage` | 10.77.7.0/24 | api (.30), video-worker (.50), storage (.40) | нет | файлы S3 |
| `admin` | 10.77.8.0/24 | rabbitmq (.40) | да, 127.0.0.1:15672 | веб-консоль RabbitMQ |

## Правила

Все правила в одном файле: [rules.conf](rules.conf). Порядок цепочки `VIDSEC`:

1. Отключённые вручную правила → DROP (обрывает и уже открытые соединения).
2. Ответные пакеты установленных соединений (`ESTABLISHED,RELATED`) → ACCEPT.
3. 13 разрешающих правил белого списка → ACCEPT.
4. Всё остальное → LOG (`VIDSEC-DROP`) и DROP.

## Команды

```bash
docker compose exec firewall fw show               # все правила, состояние и счётчики пакетов
docker compose exec firewall fw disable api-redis  # запретить легитимное правило
docker compose exec firewall fw enable api-redis   # вернуть его
docker compose exec firewall fw reset              # включить все правила
bash firewall/test.sh                              # 25 проверок соединений (в Windows — в Git Bash)
```

## Сценарий демонстрации

1. **Сегменты:** показать таблицу выше и блок `networks` в `docker-compose.yml`,
   затем `docker network ls` и `docker network inspect vidsec_cache`.
2. **Все правила:** `docker compose exec firewall fw show`.
3. **Разрешённое проходит, запрещённое нет:** `bash firewall/test.sh`. В выводе видно,
   кто кого блокирует: «пакеты отброшены firewall (DROP)» или «нет маршрута: другой сегмент сети».
   Повторный `fw show` покажет, что счётчик правила DROP вырос.
4. **Запрет легитимного правила:**
   ```bash
   docker compose exec firewall fw disable api-redis
   curl http://localhost:8080/api/health   # "redis": "error: Timeout ..."
   ```
   Главная страница в браузере показывает «Ошибка: Кеш Redis недоступен», а в `fw show`
   растёт счётчик правила `DISABLED api-redis`. Затем `fw enable api-redis`, и всё снова работает.
   Так же можно отключить `frontend-api` (через 5 с сайт отдаёт 504) или `worker-rabbitmq`
   (примерно через минуту воркер теряет связь с брокером, новые видео застревают в статусе
   «обрабатывается», задачи копятся в очереди; после `fw enable` воркер переподключается и всё обрабатывает).
5. **Без firewall:** `docker compose stop firewall` удаляет правила. Теперь, например,
   api достаёт до консоли RabbitMQ (`test.sh` покажет FAIL в блоке «Запрещено firewall»),
   а сегментация по-прежнему изолирует разные сети. Вернуть: `docker compose start firewall`.

## Как это работает технически

- Контейнер `firewall` запущен с `network_mode: host` и `cap_add: NET_ADMIN`, поэтому меняет
  iptables хоста Docker. В Docker Desktop это виртуальная машина Docker.
- Скрипт сам выбирает бэкенд iptables (nf_tables или legacy), в котором Docker держит свои правила.
- Трафик между контейнерами одной сети проходит через iptables благодаря `br_netfilter`
  (`net.bridge.bridge-nf-call-iptables=1`).
- При остановке контейнера правила удаляются. При старте загружаются заново, все включены.
