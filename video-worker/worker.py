"""video-worker: обработка загруженных видео.

1. Берёт задачу из очереди RabbitMQ `video.processing`.
2. Скачивает видео из S3 и вырезает кадр для превью через ffmpeg.
3. Кладёт превью в S3 и меняет статус видео в PostgreSQL на «ready».
Сообщение подтверждается (ack) только после успешной обработки,
поэтому при падении воркера задача не теряется и достаётся другому.
"""
import json
import os
import subprocess
import tempfile
import time

import boto3
import imageio_ffmpeg
import pika
import psycopg
from botocore.config import Config

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
QUEUE = "video.processing"
BUCKET = os.environ.get("S3_BUCKET", "videos")
# Два узла PostgreSQL: подключаемся к тому, который сейчас master
DB_DSN = (
    "host=video-db,video-db-standby target_session_attrs=read-write "
    f"dbname={os.environ['VIDEO_DB_NAME']} "
    f"user={os.environ['VIDEO_DB_USER']} password={os.environ['VIDEO_DB_PASSWORD']} connect_timeout=3"
)
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["STORAGE_URL"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
    region_name="us-east-1",
    config=Config(s3={"addressing_style": "path"}),
)
# Кластер RabbitMQ: при падении узла воркер переподключается к следующему
PARAMS = [
    pika.ConnectionParameters(
        host=node,
        credentials=pika.PlainCredentials(os.environ["RABBITMQ_USER"], os.environ["RABBITMQ_PASSWORD"]),
        heartbeat=10,
        connection_attempts=1,
        socket_timeout=3,
    )
    for node in ("rabbitmq", "rabbitmq-2", "rabbitmq-3")
]


def log(msg: str):
    print(f"video-worker: {msg}", flush=True)


def make_thumbnail(src: str, dst: str):
    """Кадр на 1-й секунде; если видео короче — первый кадр."""
    for seek in ("1", "0"):
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-ss", seek, "-i", src,
               "-frames:v", "1", "-vf", "scale=480:-2", dst]
        if subprocess.run(cmd, timeout=120).returncode == 0 and os.path.getsize(dst) > 0:
            return
    raise RuntimeError("ffmpeg не смог получить кадр из видео")


def set_status(video_id: int, status: str, thumbnail_key: str | None = None):
    with psycopg.connect(DB_DSN) as conn:
        conn.execute(
            "UPDATE videos SET status = %s, thumbnail_key = COALESCE(%s, thumbnail_key), updated_at = now() "
            "WHERE id = %s",
            (status, thumbnail_key, video_id),
        )


def handle(channel, method, _properties, body):
    task = json.loads(body)
    video_id, object_key = task["video_id"], task["object_key"]
    log(f"получена задача: видео {video_id}")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src, thumb = os.path.join(tmp, "video"), os.path.join(tmp, "thumb.jpg")
            s3.download_file(BUCKET, object_key, src)
            make_thumbnail(src, thumb)
            thumb_key = f"videos/{video_id}/thumbnail.jpg"
            s3.upload_file(thumb, BUCKET, thumb_key, ExtraArgs={"ContentType": "image/jpeg"})
        set_status(video_id, "ready", thumb_key)
        log(f"видео {video_id} обработано, статус ready")
    except s3.exceptions.NoSuchKey:
        log(f"видео {video_id} уже удалено, задача пропущена")
    except Exception as exc:  # noqa: BLE001
        log(f"ошибка обработки видео {video_id}: {exc}")
        try:
            set_status(video_id, "failed")
        except Exception:  # noqa: BLE001
            # БД недоступна — возвращаем задачу в очередь, повторим позже
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            time.sleep(5)
            return
    channel.basic_ack(delivery_tag=method.delivery_tag)


def connect():
    """Первый живой узел кластера; упавший узел может даже не резолвиться по DNS."""
    for params in PARAMS:
        try:
            return pika.BlockingConnection(params)
        except (pika.exceptions.AMQPError, OSError) as exc:
            log(f"узел {params.host} недоступен ({exc!r})")
    raise pika.exceptions.AMQPConnectionError("все узлы RabbitMQ недоступны")


def main():
    while True:
        try:
            connection = connect()
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
            channel.basic_qos(prefetch_count=1)   # по одной задаче за раз
            channel.basic_consume(queue=QUEUE, on_message_callback=handle)
            log(f"Hello, World! Подключён к RabbitMQ (узел {connection._impl.params.host}), слушаю очередь {QUEUE}")
            channel.start_consuming()
        except (pika.exceptions.AMQPError, OSError) as exc:
            log(f"нет связи с RabbitMQ ({exc!r}), переподключаюсь к другому узлу через 2 с")
            time.sleep(2)


if __name__ == "__main__":
    main()
