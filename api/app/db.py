"""Работа с PostgreSQL (video DB): пул соединений."""
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import VIDEO_DB_DSN

pool = ConnectionPool(VIDEO_DB_DSN, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=False)
