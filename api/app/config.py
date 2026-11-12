"""Настройки api из переменных окружения."""
import os

VIDEO_DB_DSN = (
    f"host=video-db dbname={os.environ['VIDEO_DB_NAME']} "
    f"user={os.environ['VIDEO_DB_USER']} password={os.environ['VIDEO_DB_PASSWORD']} "
    "connect_timeout=3"
)
REDIS_PASSWORD = os.environ["REDIS_PASSWORD"]
RABBITMQ_USER = os.environ["RABBITMQ_USER"]
RABBITMQ_PASSWORD = os.environ["RABBITMQ_PASSWORD"]
STORAGE_URL = os.environ["STORAGE_URL"]
S3_ACCESS_KEY = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY = os.environ["S3_SECRET_KEY"]
S3_BUCKET = os.environ.get("S3_BUCKET", "videos")
AUTH_URL = os.environ.get("AUTH_URL", "http://auth-service:8000")

PROCESSING_QUEUE = "video.processing"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
RECOMMENDATION_SMOOTHING = 10      # score = репутация / (просмотры + 10)
RECOMMENDATIONS_TTL = 60           # сколько секунд список рекомендаций живёт в кеше
REPUTATION_TTL = 300               # сколько секунд репутация живёт в кеше
VIEWS_FLUSH_INTERVAL = 30          # как часто счётчики просмотров переносятся из Redis в БД
