"""auth-service: регистрация, вход и выдача JWT (этап 2: заглушка)."""
import os

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="VidSec auth-service", root_path="")

USERS_DB_DSN = (
    f"host=users-db dbname={os.environ['USERS_DB_NAME']} "
    f"user={os.environ['USERS_DB_USER']} password={os.environ['USERS_DB_PASSWORD']} "
    "connect_timeout=3"
)


@app.get("/auth/hello")
def hello():
    return {"service": "auth-service", "message": "Hello, World!"}


@app.get("/auth/health")
def health():
    try:
        with psycopg.connect(USERS_DB_DSN) as conn:
            version = conn.execute("SELECT version()").fetchone()[0]
        return {"service": "auth-service", "users-db": "ok", "postgres": version.split(",")[0]}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"service": "auth-service", "users-db": f"error: {exc}"})
