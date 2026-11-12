"""auth-service: регистрация, вход и выдача JWT.

Только этот сервис видит БД пользователей и хеши паролей.
Токены подписываются закрытым ключом RSA (RS256). Остальные сервисы
получают открытый ключ через GET /auth/public-key и сами проверяют подпись.
"""
import os
import time
from pathlib import Path

import bcrypt
import jwt
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

app = FastAPI(title="VidSec auth-service")

USERS_DB_DSN = (
    f"host=users-db dbname={os.environ['USERS_DB_NAME']} "
    f"user={os.environ['USERS_DB_USER']} password={os.environ['USERS_DB_PASSWORD']} "
    "connect_timeout=3"
)
TOKEN_TTL_SECONDS = int(os.environ.get("JWT_TTL_SECONDS", "3600"))
ISSUER = "vidsec-auth"
KEY_DIR = Path("/keys")


# ---------- Ключи подписи ----------
def load_or_create_private_key():
    """Закрытый ключ хранится в отдельном томе и никогда не покидает auth-service."""
    key_file = KEY_DIR / "jwt_private.pem"
    if key_file.exists():
        return serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    return key


PRIVATE_KEY = load_or_create_private_key()
PUBLIC_KEY_PEM = PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()


# ---------- Модели ----------
class Credentials(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_]{3,32}$")
    password: str = Field(min_length=8, max_length=72)  # bcrypt учитывает только 72 байта


def db():
    return psycopg.connect(USERS_DB_DSN)


# ---------- Эндпоинты ----------
@app.get("/auth/hello")
def hello():
    return {"service": "auth-service", "message": "Hello, World!"}


@app.post("/auth/register", status_code=201)
def register(creds: Credentials):
    password_hash = bcrypt.hashpw(creds.password.encode(), bcrypt.gensalt(rounds=12)).decode()
    try:
        with db() as conn:
            user_id = conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
                (creds.username, password_hash),
            ).fetchone()[0]
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "Пользователь с таким именем уже существует")
    return {"id": user_id, "username": creds.username}


@app.post("/auth/login")
def login(creds: Credentials):
    with db() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = %s", (creds.username,)
        ).fetchone()
    # Одинаковый ответ для «нет пользователя» и «неверный пароль»: не раскрываем, какие логины существуют
    if not row or not bcrypt.checkpw(creds.password.encode(), row[1].encode()):
        raise HTTPException(401, "Неверное имя пользователя или пароль")
    now = int(time.time())
    token = jwt.encode(
        {"sub": str(row[0]), "name": creds.username, "iss": ISSUER, "iat": now, "exp": now + TOKEN_TTL_SECONDS},
        PRIVATE_KEY,
        algorithm="RS256",
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": TOKEN_TTL_SECONDS}


@app.get("/auth/public-key", response_class=PlainTextResponse)
def public_key():
    return PUBLIC_KEY_PEM


@app.get("/auth/health")
def health():
    try:
        with db() as conn:
            version = conn.execute("SELECT version()").fetchone()[0]
            users = conn.execute("SELECT count(*) FROM users").fetchone()[0]
        return {"service": "auth-service", "users-db": "ok", "postgres": version.split(",")[0], "users": users}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"service": "auth-service", "users-db": f"error: {exc}"})
