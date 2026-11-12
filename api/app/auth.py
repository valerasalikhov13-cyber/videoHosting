"""Проверка JWT. Подпись проверяется открытым ключом auth-service, сам api паролей не видит."""
import time

import httpx
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import AUTH_URL
from .db import pool

_public_key: str | None = None
bearer = HTTPBearer(auto_error=False)


def public_key() -> str:
    """Открытый ключ запрашивается у auth-service один раз и хранится в памяти."""
    global _public_key
    if _public_key is None:
        for attempt in range(10):
            try:
                resp = httpx.get(f"{AUTH_URL}/auth/public-key", timeout=3)
                resp.raise_for_status()
                _public_key = resp.text
                break
            except httpx.HTTPError:
                if attempt == 9:
                    raise HTTPException(503, "auth-service недоступен, не удалось получить ключ проверки JWT")
                time.sleep(1)
    return _public_key


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, public_key(), algorithms=["RS256"], issuer="vidsec-auth",
                          options={"require": ["exp", "sub", "iss"]})
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Срок действия токена истёк, войдите заново")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Недействительный токен")


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if creds is None:
        raise HTTPException(401, "Требуется вход")
    claims = _decode(creds.credentials)
    user = {"id": int(claims["sub"]), "name": claims["name"]}
    # Канал создаётся при первом обращении пользователя к api
    with pool.connection() as conn:
        conn.execute("INSERT INTO channels (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                     (user["id"], user["name"]))
    return user


def optional_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict | None:
    if creds is None:
        return None
    claims = _decode(creds.credentials)
    return {"id": int(claims["sub"]), "name": claims["name"]}
