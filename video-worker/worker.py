"""video-worker: обработка видео из очереди (этап 2: заглушка).

Подключается к RabbitMQ и пишет в лог hello-world.
Обработка задач появится на этапе 3.
"""
import os
import time

import pika

params = pika.ConnectionParameters(
    host="rabbitmq",
    credentials=pika.PlainCredentials(os.environ["RABBITMQ_USER"], os.environ["RABBITMQ_PASSWORD"]),
    heartbeat=30,
)

while True:
    try:
        connection = pika.BlockingConnection(params)
        print("video-worker: Hello, World! Подключён к RabbitMQ, жду задач.", flush=True)
        while True:
            connection.process_data_events(time_limit=60)
            print("video-worker: работаю, задач пока нет", flush=True)
    except pika.exceptions.AMQPError as exc:
        print(f"video-worker: нет связи с RabbitMQ ({exc}), повтор через 5 с", flush=True)
        time.sleep(5)
