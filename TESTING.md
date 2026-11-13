# Как тестировать VidSec

Все команды запускаются из папки проекта (там, где лежит `docker-compose.yml`).
Они одинаково работают в PowerShell, cmd и bash. Пароли в примерах взяты из `.env.example`.
Если вы поменяли их в `.env`, подставьте свои.

## 1. Запуск и состояние контейнеров

```bash
docker compose up -d --build      # собрать и запустить
docker compose ps                 # у всех STATUS = Up, у баз и брокера ещё (healthy)
docker compose logs api           # логи одного сервиса
docker compose logs -f video-worker   # логи в реальном времени (выход: Ctrl+C)
docker compose logs --tail 50     # последние 50 строк всех сервисов
```

Если сервис в статусе `Restarting` или `Exited`, причина почти всегда в его логах.

## 2. Автотест: 32 проверки одной командой

Тест проходит через nginx так же, как браузер: регистрация, вход, загрузка видео,
обработка воркером, просмотры, лайки, рекомендации, права владельца, комментарии, удаление.
Python ставить не нужно, тест запускается в одноразовом контейнере:

```bash
docker run --rm --add-host=host.docker.internal:host-gateway -v "${PWD}/tests:/tests" python:3.12-slim python /tests/smoke_test.py
```

В конце должно быть `Итого: пройдено 32, ошибок 0`. Каждый запуск создаёт новых пользователей,
поэтому тест можно гонять сколько угодно раз.

## 3. Ручная проверка в браузере

1. Откройте <http://localhost:8080>, нажмите «Регистрация» и создайте пользователя.
2. «Загрузить видео»: выберите любой mp4 (подойдёт `tests/sample.mp4`). Откроется страница видео
   со статусом «обрабатывается», через пару секунд появится плеер.
3. На главной видео появится в «Новых» и в «Рекомендациях». Под заголовком написано, взят
   список из кеша Redis (hit) или посчитан в БД (miss).
4. В другом браузере или в окне инкогнито зарегистрируйте второго пользователя, поставьте лайк
   и оставьте комментарий. Репутация видео и канала изменится сразу.
5. Вторым пользователем попробуйте открыть чужое видео: кнопок «Сохранить» и «Удалить» нет,
   а прямой запрос к API вернёт 403.
6. Страница «Состояние системы» проверяет связь сервисов с базами, кешем, брокером и хранилищем.

## 4. PostgreSQL: что лежит в базах

### База пользователей (users-db, доступна только auth-service)

```bash
docker compose exec users-db psql -U auth_service -d users -c "SELECT id, username, left(password_hash, 20) AS hash, created_at FROM users"
```

Видно, что пароли хранятся только как bcrypt-хеши (`$2b$12$...`).

### База видео (video-db)

```bash
# Список таблиц и представлений
docker compose exec video-db psql -U video_service -d videos -c "\d"

# Видео
docker compose exec video-db psql -U video_service -d videos -c "SELECT id, channel_id, title, status, views, created_at FROM videos ORDER BY id"

# Каналы и их репутация (сумма репутаций видео)
docker compose exec video-db psql -U video_service -d videos -c "SELECT c.id, c.name, cr.reputation FROM channels c JOIN channel_reputation cr ON cr.channel_id = c.id"

# Репутация каждого видео (лайки минус дизлайки)
docker compose exec video-db psql -U video_service -d videos -c "SELECT * FROM video_reputation"

# Оценки и комментарии
docker compose exec video-db psql -U video_service -d videos -c "SELECT * FROM votes"
docker compose exec video-db psql -U video_service -d videos -c "SELECT id, video_id, author_id, text FROM comments"

# Рекомендации прямо в SQL, по той же формуле, что в api
docker compose exec video-db psql -U video_service -d videos -c "SELECT v.id, v.title, vr.reputation, v.views, round(vr.reputation::numeric / (v.views + 10), 4) AS score FROM videos v JOIN video_reputation vr ON vr.video_id = v.id WHERE v.status = 'ready' ORDER BY score DESC"
```

### Интерактивная консоль psql

```bash
docker compose exec video-db psql -U video_service -d videos
```

Внутри: `\dt` показывает таблицы, `\d videos` структуру таблицы, `\dv` представления,
любой SQL-запрос заканчивается `;`, выход `\q`.

### Как показать insert / update / delete

Выполните действие в браузере и сразу повторите SELECT:

| Действие в приложении | SQL в api | Что проверить |
|---|---|---|
| загрузить видео | `INSERT INTO videos` | новая строка в `videos` со статусом `processing`, затем `ready` (UPDATE из воркера) |
| изменить название | `UPDATE videos` | изменились `title` и `updated_at` |
| лайк, затем дизлайк, затем снять | `INSERT`, `UPDATE`, `DELETE` в `votes` | строка появилась, `value` сменился с 1 на -1, строка исчезла |
| удалить видео | `DELETE FROM videos` | строка пропала, оценки и комментарии удалились каскадно |

## 5. Redis: кеш

```bash
# Все ключи
docker compose exec redis redis-cli -a change-me-redis --no-auth-warning KEYS "*"

# Счётчик просмотров видео 1 (INCR при каждом открытии страницы)
docker compose exec redis redis-cli -a change-me-redis --no-auth-warning GET views:1

# Кеш рекомендаций: содержимое и сколько секунд ему осталось жить (из 60)
docker compose exec redis redis-cli -a change-me-redis --no-auth-warning GET recommendations
docker compose exec redis redis-cli -a change-me-redis --no-auth-warning TTL recommendations

# Кеш репутации
docker compose exec redis redis-cli -a change-me-redis --no-auth-warning GET rep:video:1
docker compose exec redis redis-cli -a change-me-redis --no-auth-warning GET rep:channel:1

# Смотреть все команды к Redis в реальном времени (обновите страницу видео в браузере)
docker compose exec redis redis-cli -a change-me-redis --no-auth-warning MONITOR
```

Что показывает:
- `views:<id>` растёт при каждом открытии страницы видео. Раз в 30 секунд api переносит
  значения в столбец `videos.views` (в логах api строка «Сброшено в БД счётчиков просмотров»).
- `recommendations` появляется после открытия главной и исчезает через 60 секунд.
- `rep:video:<id>` и `rep:channel:<id>` удаляются при каждом лайке или дизлайке и считаются заново.
- Без пароля Redis не отвечает: `docker compose exec redis redis-cli KEYS "*"` вернёт `NOAUTH`.

## 6. RabbitMQ: брокер сообщений

```bash
# Очереди: имя, сообщений в очереди, сколько потребителей слушает
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers

# Подключения к брокеру (видно api и video-worker)
docker compose exec rabbitmq rabbitmqctl list_connections user peer_host state

# Логи воркера: получил задачу, обработал
docker compose logs video-worker
```

Веб-консоль: <http://localhost:15672>, логин `vidsec`, пароль `change-me-rabbit`.
Вкладка Queues → `video.processing`.

### Демонстрация, что задачи не теряются

```bash
docker compose stop video-worker
# загрузите видео в браузере: оно останется в статусе «обрабатывается»
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers
# video.processing  1  0   <- задача ждёт в очереди, потребителей нет
docker compose start video-worker
docker compose logs --tail 5 video-worker
# воркер подключился, забрал задачу, видео стало ready
```

## 7. Хранилище файлов (S3, SeaweedFS)

```bash
# Бакет и его размер
docker compose exec storage sh -c "echo s3.bucket.list | weed shell"

# Файлы видео 1: оригинал и превью
docker compose exec storage sh -c "echo 'fs.ls -l /buckets/videos/videos/1' | weed shell"
```

Анонимного доступа нет. Запрос без ключа получает 403:

```bash
docker compose exec api python -c "import httpx; print(httpx.get('http://storage:8333/videos/videos/1/original').status_code)"
```

## 8. Проверки безопасности из консоли

Удобнее всего их запускать автотестом (раздел 2). Там уже есть: неверный пароль → 401,
поддельный JWT → 401, запрос без токена → 401, загрузка не видео → 415, изменение и удаление
чужого видео → 403, удаление чужого комментария → 404.

Посмотреть заголовки безопасности nginx (CSP, X-Frame-Options и другие):

```bash
curl -I http://localhost:8080/
```

В Windows PowerShell 5 вместо `curl` пишите `curl.exe`.

## 9. Сегментация сети и firewall (этап 4)

```bash
docker compose exec firewall fw show                # все правила и счётчики пакетов
bash firewall/test.sh                               # 25 проверок соединений (в Windows — в Git Bash)
docker compose exec firewall fw disable api-redis   # запретить легитимное правило -> ошибки
docker compose exec firewall fw enable api-redis    # вернуть
docker network ls                                   # сегменты vidsec_*
docker network inspect vidsec_cache                 # кто подключён к сегменту
```

Подробно: [firewall/README.md](firewall/README.md).

## 10. Сброс к чистому состоянию

```bash
docker compose down -v            # удалить контейнеры и ВСЕ данные (базы, кеш, файлы, ключ JWT)
docker compose up -d --build      # поднять заново, схемы БД создадутся автоматически
```
