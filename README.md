# VidSec — видеохостинг на микросервисах

Учебный проект по курсу «Безопасность высоконагруженных систем».
Пользователь заводит канал, загружает видео, смотрит чужие и ставит им лайк или дизлайк.
Репутация видео — разница лайков и дизлайков, репутация канала — сумма репутаций его видео.

## Компоненты

| Сервис | Что делает | Технологии |
|---|---|---|
| `frontend` | веб-интерфейс, единственная точка входа (порт 8080), проксирует `/auth/` и `/api/` | nginx |
| `auth-service` | регистрация, вход, выдача JWT | Python, FastAPI |
| `api` | видео, каналы, комментарии, оценки, рекомендации | Python, FastAPI |
| `video-worker` | фоновая обработка видео из очереди (превью) | Python, pika, ffmpeg (imageio-ffmpeg) |
| `users-db` | БД пользователей, доступна только `auth-service` | PostgreSQL 16 |
| `video-db` | БД видео, каналов, оценок | PostgreSQL 16 |
| `redis` | кеш: просмотры, рекомендации, репутация | Redis 7 |
| `rabbitmq` | брокер сообщений: задачи на обработку видео | RabbitMQ 3.13 |
| `storage` | S3-совместимое хранилище видеофайлов и превью | SeaweedFS |

## Сборка и запуск (деплой)

Нужен Docker Desktop (или Docker Engine с плагином compose).

```bash
git clone https://github.com/valerasalikhov13-cyber/videoHosting.git
cd videoHosting

# 1. Создать файл с паролями (Windows PowerShell: copy .env.example .env)
cp .env.example .env

# 2. Собрать образы и запустить все контейнеры
docker compose up -d --build

# 3. Проверить, что все контейнеры запущены
docker compose ps
```

После запуска:

- приложение: <http://localhost:8080>
- консоль RabbitMQ: <http://localhost:15672> (логин и пароль из `.env`)

## Тестирование

Подробная инструкция: [TESTING.md](TESTING.md). Автотест на 32 проверки одной командой:

```bash
docker run --rm --add-host=host.docker.internal:host-gateway -v "${PWD}/tests:/tests" python:3.12-slim python /tests/smoke_test.py
```

## Функции

1. Регистрация и вход (auth-service, пароли в виде bcrypt-хешей, токен JWT RS256).
2. Загрузка видео с названием и описанием.
3. Лента новых видео с превью.
4. Страница видео с плеером (поддерживается перемотка).
5. Счётчик просмотров в Redis.
6. Редактирование и удаление своего видео.
7. Фоновая обработка через RabbitMQ: video-worker делает превью через ffmpeg.
8. Комментарии.
9. Репутация: у видео — лайки минус дизлайки, у канала — сумма репутаций его видео.
10. Рекомендации на главной: `score = репутация / (просмотры + 10)`, список кешируется в Redis на 60 с.

## Как сервисы работают с хранилищами (этап 3)

| Хранилище | Кто пишет и читает | Что делает |
|---|---|---|
| PostgreSQL `users-db` | auth-service | INSERT пользователя при регистрации, SELECT при входе |
| PostgreSQL `video-db` | api, video-worker | INSERT/SELECT/UPDATE/DELETE видео, оценок, комментариев; worker делает UPDATE статуса |
| Redis | api | `views:<id>` — счётчик просмотров (INCR, раз в 30 с сбрасывается в БД); `recommendations` — кеш рекомендаций на 60 с; `rep:video:<id>`, `rep:channel:<id>` — кеш репутации |
| RabbitMQ | api публикует, video-worker потребляет | очередь `video.processing` (durable, persistent-сообщения, ack после обработки) |
| S3 (SeaweedFS) | api, video-worker | видеофайлы и превью; доступ только по ключу из `.env` |

SQL-схемы: [`db/users-schema.sql`](db/users-schema.sql) и [`db/video-schema.sql`](db/video-schema.sql).
PostgreSQL выполняет их автоматически при первом запуске (на пустом томе).

> Если раньше уже запускалась версия этапа 2, пересоздайте тома, чтобы применились схемы:
> `docker compose down -v`, затем `docker compose up -d --build`.

## API

| Метод и путь | Доступ | Что делает |
|---|---|---|
| `POST /auth/register` | все | регистрация `{username, password}` |
| `POST /auth/login` | все | вход, возвращает `access_token` |
| `GET /auth/public-key` | все | открытый ключ для проверки JWT |
| `GET /api/videos` | все | лента новых видео |
| `GET /api/recommendations` | все | рекомендации, поле `cache` = `hit` или `miss` |
| `POST /api/videos` | вход | загрузка (multipart: `title`, `description`, `file`) |
| `GET /api/videos/{id}` | все | данные видео, репутация, моя оценка |
| `PATCH /api/videos/{id}` | владелец | изменить название или описание |
| `DELETE /api/videos/{id}` | владелец | удалить видео |
| `GET /api/videos/{id}/stream` | все | видеофайл (поддерживает Range) |
| `GET /api/videos/{id}/thumbnail` | все | превью |
| `POST /api/videos/{id}/view` | все | +1 просмотр |
| `POST /api/videos/{id}/vote` | вход | `{value: 1 / -1 / 0}` — лайк, дизлайк, снять оценку |
| `GET/POST /api/videos/{id}/comments` | все / вход | комментарии |
| `DELETE /api/comments/{id}` | автор | удалить комментарий |
| `GET /api/channels/{id}` | все | канал, его репутация и видео |
| `GET /api/me` | вход | текущий пользователь и репутация его канала |
| `GET /api/health`, `GET /auth/health` | все | проверка связи с БД, кешем, брокером, хранилищем |

## Как показать работу хранилищ

```bash
# PostgreSQL: таблицы и данные
docker compose exec video-db psql -U video_service -d videos -c "\dt"
docker compose exec video-db psql -U video_service -d videos -c "SELECT id, title, status, views FROM videos"
docker compose exec users-db psql -U auth_service -d users -c "SELECT id, username, left(password_hash, 20) FROM users"

# Redis: ключи кеша (пароль из .env)
docker compose exec redis redis-cli -a change-me-redis --no-auth-warning KEYS '*'

# RabbitMQ: очередь и логи обработки
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers
docker compose logs -f video-worker
```

Консоль RabbitMQ: <http://localhost:15672>. Страница «Состояние системы» в приложении отправляет health-запросы кнопками.

## Остановка

```bash
docker compose down        # остановить, данные сохранятся
docker compose down -v     # остановить и удалить все данные
```
