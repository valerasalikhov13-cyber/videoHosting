"""Работа с Redis: счётчики просмотров, кеш рекомендаций и репутации."""
import json
import logging
import threading
import time

import redis

from .config import REDIS_PASSWORD, REPUTATION_TTL, VIEWS_FLUSH_INTERVAL
from .db import pool

log = logging.getLogger("api.cache")
r = redis.Redis(host="redis", password=REDIS_PASSWORD, socket_timeout=3, decode_responses=True)


# ---------- Просмотры (write-behind: пишем в Redis, раз в 30 с переносим в БД) ----------
def add_view(video_id: int, db_views: int) -> int:
    key = f"views:{video_id}"
    r.set(key, db_views, nx=True)       # первый раз берём значение из БД
    return r.incr(key)


def get_views(video_id: int, db_views: int) -> int:
    value = r.get(f"views:{video_id}")
    return int(value) if value is not None else db_views


def flush_views_forever():
    while True:
        time.sleep(VIEWS_FLUSH_INTERVAL)
        try:
            keys = list(r.scan_iter("views:*"))
            if not keys:
                continue
            values = r.mget(keys)
            rows = [(int(v), int(k.split(":")[1])) for k, v in zip(keys, values) if v is not None]
            with pool.connection() as conn:
                conn.cursor().executemany("UPDATE videos SET views = %s WHERE id = %s", rows)
            log.info("Сброшено в БД счётчиков просмотров: %d", len(rows))
        except Exception:  # noqa: BLE001
            log.exception("Не удалось сбросить просмотры в БД")


def start_views_flusher():
    threading.Thread(target=flush_views_forever, daemon=True).start()


# ---------- Простой JSON-кеш ----------
def cached(key: str, ttl: int, compute):
    """Возвращает (значение, из_кеша). При промахе считает значение и кладёт его в Redis на ttl секунд."""
    hit = r.get(key)
    if hit is not None:
        return json.loads(hit), True
    value = compute()
    r.set(key, json.dumps(value, default=str), ex=ttl)
    return value, False


def invalidate_reputation(video_id: int, channel_id: int):
    r.delete(f"rep:video:{video_id}", f"rep:channel:{channel_id}")


def video_reputation(video_id: int) -> int:
    def compute():
        with pool.connection() as conn:
            row = conn.execute("SELECT reputation FROM video_reputation WHERE video_id = %s", (video_id,)).fetchone()
        return row["reputation"] if row else 0

    return cached(f"rep:video:{video_id}", REPUTATION_TTL, compute)[0]


def channel_reputation(channel_id: int) -> int:
    def compute():
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT reputation FROM channel_reputation WHERE channel_id = %s", (channel_id,)
            ).fetchone()
        return row["reputation"] if row else 0

    return cached(f"rep:channel:{channel_id}", REPUTATION_TTL, compute)[0]
