"""api: видео, каналы, комментарии, репутация и рекомендации.

Хранилища:
  PostgreSQL (video DB) — метаданные видео, каналы, оценки, комментарии;
  Redis                 — счётчики просмотров, кеш рекомендаций и репутации;
  RabbitMQ              — очередь задач на обработку видео для video-worker;
  S3 (SeaweedFS)        — видеофайлы и превью.
"""
import logging
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import broker, cache, storage
from .auth import current_user, optional_user
from .config import MAX_UPLOAD_BYTES, RECOMMENDATION_SMOOTHING, RECOMMENDATIONS_TTL
from .db import pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("api")
app = FastAPI(title="VidSec api")


@app.on_event("startup")
def startup():
    pool.open(wait=True, timeout=30)
    storage.ensure_bucket()
    cache.start_views_flusher()


# ---------- Вспомогательное ----------
VIDEO_COLUMNS = """
    v.id, v.channel_id, c.name AS channel_name, v.title, v.description, v.status,
    v.thumbnail_key IS NOT NULL AS has_thumbnail, v.views, v.created_at, v.updated_at
"""


def load_video(video_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            f"SELECT {VIDEO_COLUMNS}, v.object_key, v.thumbnail_key "
            "FROM videos v JOIN channels c ON c.id = v.channel_id WHERE v.id = %s",
            (video_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Видео не найдено")
    return row


def public(video: dict) -> dict:
    """Убирает внутренние поля и подставляет актуальные просмотры из Redis."""
    data = {k: v for k, v in video.items() if k not in ("object_key", "thumbnail_key", "score")}
    data["views"] = cache.get_views(video["id"], video["views"])
    return data


def require_owner(video: dict, user: dict):
    if video["channel_id"] != user["id"]:
        raise HTTPException(403, "Изменять и удалять видео может только владелец канала")


# ---------- Служебные ----------
@app.get("/api/hello")
def hello():
    return {"service": "api", "message": "Hello, World!"}


@app.get("/api/health")
def health():
    def check_db():
        with pool.connection() as conn:
            return conn.execute("SELECT version()").fetchone()["version"].split(",")[0]

    checks = {
        "video-db": check_db,
        "redis": lambda: "redis " + cache.r.info("server")["redis_version"],
        "rabbitmq": lambda: "rabbitmq " + broker.server_version(),
        "storage": lambda: f"s3 bucket '{storage.S3_BUCKET}' доступен" if storage.s3.head_bucket(Bucket=storage.S3_BUCKET) else "",
    }
    result, ok = {"service": "api"}, True
    for name, fn in checks.items():
        try:
            result[name] = "ok: " + fn()
        except Exception as exc:  # noqa: BLE001
            ok = False
            result[name] = f"error: {exc}"
    return JSONResponse(status_code=200 if ok else 503, content=result)


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return {**user, "reputation": cache.channel_reputation(user["id"])}


# ---------- Лента и рекомендации ----------
@app.get("/api/videos")
def list_videos(limit: int = 20):
    """Лента: новые готовые видео (SELECT)."""
    with pool.connection() as conn:
        rows = conn.execute(
            f"SELECT {VIDEO_COLUMNS} FROM videos v JOIN channels c ON c.id = v.channel_id "
            "WHERE v.status = 'ready' ORDER BY v.created_at DESC LIMIT %s",
            (min(limit, 100),),
        ).fetchall()
    return [public(r) for r in rows]


@app.get("/api/recommendations")
def recommendations():
    """Рекомендации: score = репутация / (просмотры + 10). Список кешируется в Redis на 60 с."""

    def compute():
        with pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {VIDEO_COLUMNS}, vr.reputation, "
                "vr.reputation::float / (v.views + %s) AS score "
                "FROM videos v JOIN channels c ON c.id = v.channel_id "
                "JOIN video_reputation vr ON vr.video_id = v.id "
                "WHERE v.status = 'ready' ORDER BY score DESC, v.created_at DESC LIMIT 10",
                (RECOMMENDATION_SMOOTHING,),
            ).fetchall()
        return [{**r, "score": round(r["score"], 4)} for r in rows]

    items, from_cache = cache.cached("recommendations", RECOMMENDATIONS_TTL, compute)
    return {"cache": "hit" if from_cache else "miss", "formula": "reputation / (views + 10)", "items": items}


# ---------- Видео: CRUD ----------
@app.post("/api/videos", status_code=201)
def upload_video(
    title: str = Form(min_length=1, max_length=200),
    description: str = Form("", max_length=5000),
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
):
    """Загрузка: файл → S3, запись → PostgreSQL (INSERT), задача → RabbitMQ."""
    if not (file.content_type or "").startswith("video/"):
        raise HTTPException(415, "Можно загружать только видеофайлы")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size == 0 or size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Размер файла должен быть от 1 байта до 200 МБ")

    with pool.connection() as conn:
        video_id = conn.execute(
            "INSERT INTO videos (channel_id, title, description, object_key) VALUES (%s, %s, %s, '') RETURNING id",
            (user["id"], title.strip(), description.strip()),
        ).fetchone()["id"]
        object_key = f"videos/{video_id}/original"
        storage.upload(file.file, object_key, file.content_type)
        conn.execute("UPDATE videos SET object_key = %s WHERE id = %s", (object_key, video_id))

    broker.publish_processing_task(video_id, object_key)
    return {"id": video_id, "status": "processing"}


@app.get("/api/videos/{video_id}")
def get_video(video_id: int, user: dict | None = Depends(optional_user)):
    video = load_video(video_id)
    data = public(video)
    data["reputation"] = cache.video_reputation(video_id)
    data["channel_reputation"] = cache.channel_reputation(video["channel_id"])
    data["my_vote"] = 0
    if user:
        with pool.connection() as conn:
            row = conn.execute("SELECT value FROM votes WHERE video_id = %s AND user_id = %s",
                               (video_id, user["id"])).fetchone()
        data["my_vote"] = row["value"] if row else 0
    return data


class VideoUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)


@app.patch("/api/videos/{video_id}")
def update_video(video_id: int, body: VideoUpdate, user: dict = Depends(current_user)):
    """Редактирование своего видео (UPDATE)."""
    video = load_video(video_id)
    require_owner(video, user)
    with pool.connection() as conn:
        conn.execute(
            "UPDATE videos SET title = COALESCE(%s, title), description = COALESCE(%s, description), "
            "updated_at = now() WHERE id = %s",
            (body.title, body.description, video_id),
        )
    cache.r.delete("recommendations")
    return public(load_video(video_id))


@app.delete("/api/videos/{video_id}", status_code=204)
def delete_video(video_id: int, user: dict = Depends(current_user)):
    """Удаление своего видео (DELETE): строка в БД, файлы в S3, ключи в Redis."""
    video = load_video(video_id)
    require_owner(video, user)
    with pool.connection() as conn:
        conn.execute("DELETE FROM videos WHERE id = %s", (video_id,))
    storage.delete(video["object_key"], video["thumbnail_key"])
    cache.r.delete(f"views:{video_id}", "recommendations")
    cache.invalidate_reputation(video_id, video["channel_id"])


# ---------- Видео: файлы ----------
@app.get("/api/videos/{video_id}/stream")
def stream_video(video_id: int, request: Request):
    """Отдаёт видео из S3 с поддержкой Range, чтобы в плеере работала перемотка."""
    video = load_video(video_id)
    if video["status"] != "ready":
        raise HTTPException(409, "Видео ещё обрабатывается")
    byte_range = request.headers.get("range")
    obj = storage.get(video["object_key"], byte_range)
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(obj["ContentLength"])}
    if "ContentRange" in obj:
        headers["Content-Range"] = obj["ContentRange"]
    return StreamingResponse(
        obj["Body"].iter_chunks(64 * 1024),
        status_code=206 if byte_range else 200,
        media_type=obj.get("ContentType", "video/mp4"),
        headers=headers,
    )


@app.get("/api/videos/{video_id}/thumbnail")
def thumbnail(video_id: int):
    video = load_video(video_id)
    if not video["thumbnail_key"]:
        raise HTTPException(404, "Превью ещё не готово")
    obj = storage.get(video["thumbnail_key"])
    return StreamingResponse(obj["Body"].iter_chunks(), media_type="image/jpeg",
                             headers={"Cache-Control": "public, max-age=300"})


# ---------- Просмотры ----------
@app.post("/api/videos/{video_id}/view")
def add_view(video_id: int):
    """Счётчик просмотров живёт в Redis (INCR), в БД переносится раз в 30 с."""
    video = load_video(video_id)
    return {"views": cache.add_view(video_id, video["views"])}


# ---------- Репутация ----------
class Vote(BaseModel):
    value: Literal[-1, 0, 1]   # 1 — лайк, -1 — дизлайк, 0 — снять оценку


@app.post("/api/videos/{video_id}/vote")
def vote(video_id: int, body: Vote, user: dict = Depends(current_user)):
    video = load_video(video_id)
    with pool.connection() as conn:
        if body.value == 0:
            conn.execute("DELETE FROM votes WHERE video_id = %s AND user_id = %s", (video_id, user["id"]))
        else:
            conn.execute(
                "INSERT INTO votes (video_id, user_id, value) VALUES (%s, %s, %s) "
                "ON CONFLICT (video_id, user_id) DO UPDATE SET value = EXCLUDED.value, created_at = now()",
                (video_id, user["id"], body.value),
            )
    cache.invalidate_reputation(video_id, video["channel_id"])
    return {
        "my_vote": body.value,
        "reputation": cache.video_reputation(video_id),
        "channel_reputation": cache.channel_reputation(video["channel_id"]),
    }


# ---------- Комментарии ----------
class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.get("/api/videos/{video_id}/comments")
def list_comments(video_id: int):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT cm.id, cm.author_id, c.name AS author_name, cm.text, cm.created_at "
            "FROM comments cm JOIN channels c ON c.id = cm.author_id "
            "WHERE cm.video_id = %s ORDER BY cm.created_at",
            (video_id,),
        ).fetchall()


@app.post("/api/videos/{video_id}/comments", status_code=201)
def add_comment(video_id: int, body: CommentIn, user: dict = Depends(current_user)):
    load_video(video_id)
    with pool.connection() as conn:
        return conn.execute(
            "INSERT INTO comments (video_id, author_id, text) VALUES (%s, %s, %s) "
            "RETURNING id, author_id, %s AS author_name, text, created_at",
            (video_id, user["id"], body.text.strip(), user["name"]),
        ).fetchone()


@app.delete("/api/comments/{comment_id}", status_code=204)
def delete_comment(comment_id: int, user: dict = Depends(current_user)):
    with pool.connection() as conn:
        deleted = conn.execute("DELETE FROM comments WHERE id = %s AND author_id = %s RETURNING id",
                               (comment_id, user["id"])).fetchone()
    if not deleted:
        raise HTTPException(404, "Комментарий не найден или принадлежит другому пользователю")


# ---------- Каналы ----------
@app.get("/api/channels/{channel_id}")
def get_channel(channel_id: int, user: dict | None = Depends(optional_user)):
    with pool.connection() as conn:
        channel = conn.execute("SELECT id, name, created_at FROM channels WHERE id = %s", (channel_id,)).fetchone()
        if not channel:
            raise HTTPException(404, "Канал не найден")
        own = user is not None and user["id"] == channel_id
        rows = conn.execute(
            f"SELECT {VIDEO_COLUMNS}, vr.reputation FROM videos v JOIN channels c ON c.id = v.channel_id "
            "JOIN video_reputation vr ON vr.video_id = v.id "
            "WHERE v.channel_id = %s AND (v.status = 'ready' OR %s) ORDER BY v.created_at DESC",
            (channel_id, own),
        ).fetchall()
    return {**channel, "reputation": cache.channel_reputation(channel_id), "videos": [public(r) for r in rows]}


@app.exception_handler(psycopg.OperationalError)
def db_unavailable(_request, exc):
    log.error("БД недоступна: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "База данных недоступна"})
