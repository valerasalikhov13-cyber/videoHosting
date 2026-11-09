"""api: видео, каналы, оценки (этап 2: заглушка и проверка инфраструктуры)."""
import os

import httpx
import pika
import psycopg
import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="VidSec api")

VIDEO_DB_DSN = (
    f"host=video-db dbname={os.environ['VIDEO_DB_NAME']} "
    f"user={os.environ['VIDEO_DB_USER']} password={os.environ['VIDEO_DB_PASSWORD']} "
    "connect_timeout=3"
)


@app.get("/api/hello")
def hello():
    return {"service": "api", "message": "Hello, World!"}


def check_video_db() -> str:
    with psycopg.connect(VIDEO_DB_DSN) as conn:
        return conn.execute("SELECT version()").fetchone()[0].split(",")[0]


def check_redis() -> str:
    r = redis.Redis(host="redis", password=os.environ["REDIS_PASSWORD"], socket_timeout=3)
    r.ping()
    return "redis " + r.info("server")["redis_version"]


def check_rabbitmq() -> str:
    params = pika.ConnectionParameters(
        host="rabbitmq",
        credentials=pika.PlainCredentials(os.environ["RABBITMQ_USER"], os.environ["RABBITMQ_PASSWORD"]),
        socket_timeout=3,
        blocked_connection_timeout=3,
    )
    conn = pika.BlockingConnection(params)
    version = conn._impl.server_properties.get("version", b"")
    if isinstance(version, bytes):
        version = version.decode()
    conn.close()
    return "rabbitmq " + version


def check_storage() -> str:
    r = httpx.get(os.environ["STORAGE_URL"], timeout=3)
    return f"s3 HTTP {r.status_code}"


@app.get("/api/health")
def health():
    checks = {
        "video-db": check_video_db,
        "redis": check_redis,
        "rabbitmq": check_rabbitmq,
        "storage": check_storage,
    }
    result, ok = {"service": "api"}, True
    for name, fn in checks.items():
        try:
            result[name] = "ok: " + fn()
        except Exception as exc:  # noqa: BLE001
            ok = False
            result[name] = f"error: {exc}"
    return JSONResponse(status_code=200 if ok else 503, content=result)
