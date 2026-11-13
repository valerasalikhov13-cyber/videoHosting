"""Работа с PostgreSQL (video DB): пул соединений."""
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import VIDEO_DB_DSN

# check: перед выдачей соединение проверяется, мёртвые (например, после падения master) заменяются новыми
pool = ConnectionPool(VIDEO_DB_DSN, min_size=1, max_size=10, kwargs={"row_factory": dict_row},
                      check=ConnectionPool.check_connection, open=False)
