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
| `video-worker` | фоновая обработка видео из очереди (превью) | Python, pika, ffmpeg |
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

## Проверка (этап 2)

```bash
curl http://localhost:8080/api/hello     # заглушка api
curl http://localhost:8080/auth/hello    # заглушка auth-service
curl http://localhost:8080/api/health    # api видит video-db, redis, rabbitmq, storage
curl http://localhost:8080/auth/health   # auth-service видит users-db
docker compose logs video-worker         # worker подключился к RabbitMQ
```

Те же запросы можно отправить кнопками на главной странице.

## Остановка

```bash
docker compose down        # остановить, данные сохранятся
docker compose down -v     # остановить и удалить все данные
```
