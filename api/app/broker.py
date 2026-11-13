"""Работа с RabbitMQ: публикация задач на обработку видео."""
import json
import logging

import pika

from .config import PROCESSING_QUEUE, RABBITMQ_NODES, RABBITMQ_PASSWORD, RABBITMQ_USER

log = logging.getLogger("api.broker")
# Список узлов кластера: pika пробует их по очереди, пока не подключится к живому
PARAMS = [
    pika.ConnectionParameters(
        host=node,
        credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD),
        socket_timeout=3,
        blocked_connection_timeout=5,
        connection_attempts=1,
    )
    for node in RABBITMQ_NODES
]
QUEUE_ARGS = {"x-queue-type": "quorum"}


def connect() -> pika.BlockingConnection:
    """Подключение к первому живому узлу. Упавший контейнер даже не резолвится по DNS,
    поэтому ошибки сокета тоже означают «пробуем следующий узел»."""
    last_exc = None
    for params in PARAMS:
        try:
            return pika.BlockingConnection(params)
        except (pika.exceptions.AMQPError, OSError) as exc:
            last_exc = exc
            log.warning("Узел RabbitMQ %s недоступен: %r", params.host, exc)
    raise pika.exceptions.AMQPConnectionError(f"Все узлы RabbitMQ недоступны: {last_exc!r}")


def publish_processing_task(video_id: int, object_key: str):
    """Задача кладётся в quorum-очередь persistent-сообщением с подтверждением от брокера:
    publish считается успешным, только когда сообщение записано на большинство узлов кластера."""
    connection = connect()
    try:
        channel = connection.channel()
        channel.confirm_delivery()
        channel.queue_declare(queue=PROCESSING_QUEUE, durable=True, arguments=QUEUE_ARGS)
        channel.basic_publish(
            exchange="",
            routing_key=PROCESSING_QUEUE,
            body=json.dumps({"video_id": video_id, "object_key": object_key}),
            properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent, content_type="application/json"),
        )
        log.info("Опубликована задача обработки видео %s (узел %s)", video_id, connection._impl.params.host)
    finally:
        connection.close()


def server_version() -> str:
    connection = connect()
    version = connection._impl.server_properties.get("version", b"")
    node = connection._impl.params.host
    connection.close()
    version = version.decode() if isinstance(version, bytes) else version
    return f"{version} (узел {node})"
