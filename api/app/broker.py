"""Работа с RabbitMQ: публикация задач на обработку видео."""
import json
import logging

import pika

from .config import PROCESSING_QUEUE, RABBITMQ_PASSWORD, RABBITMQ_USER

log = logging.getLogger("api.broker")
PARAMS = pika.ConnectionParameters(
    host="rabbitmq",
    credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD),
    socket_timeout=3,
    blocked_connection_timeout=5,
)


def publish_processing_task(video_id: int, object_key: str):
    """Задача кладётся в durable-очередь persistent-сообщением, чтобы пережить перезапуск брокера."""
    connection = pika.BlockingConnection(PARAMS)
    try:
        channel = connection.channel()
        channel.queue_declare(queue=PROCESSING_QUEUE, durable=True)
        channel.basic_publish(
            exchange="",
            routing_key=PROCESSING_QUEUE,
            body=json.dumps({"video_id": video_id, "object_key": object_key}),
            properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent, content_type="application/json"),
        )
        log.info("Опубликована задача обработки видео %s", video_id)
    finally:
        connection.close()


def server_version() -> str:
    connection = pika.BlockingConnection(PARAMS)
    version = connection._impl.server_properties.get("version", b"")
    connection.close()
    return version.decode() if isinstance(version, bytes) else version
