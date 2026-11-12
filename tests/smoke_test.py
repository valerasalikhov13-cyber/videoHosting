"""Автотест VidSec: проходит основные сценарии через nginx, как настоящий пользователь.

Зависимостей нет, только стандартная библиотека Python. Запуск через Docker (из папки проекта):
  docker run --rm --add-host=host.docker.internal:host-gateway -v "${PWD}/tests:/tests" python:3.12-slim python /tests/smoke_test.py
Адрес приложения по умолчанию http://host.docker.internal:8080, можно передать первым аргументом.
"""
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://host.docker.internal:8080"
SAMPLE = Path(__file__).with_name("sample.mp4")
passed = failed = 0


def request(method, path, body=None, token=None, headers=None, raw=False):
    hdrs = dict(headers or {})
    data = None
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    elif isinstance(body, bytes):
        data = body
    if token:
        hdrs["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            status, resp_headers = resp.status, resp.headers
    except urllib.error.HTTPError as err:
        content, status, resp_headers = err.read(), err.code, err.headers
    if raw:
        return status, content, resp_headers
    try:
        return status, json.loads(content) if content else None
    except ValueError:
        return status, content.decode(errors="replace")


def multipart(fields, file_field, filename, file_bytes, content_type):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode() + file_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def check(name, condition, details=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {details}")


def section(title):
    print(f"\n== {title}")


suffix = uuid.uuid4().hex[:6]
alice, bob, password = f"alice_{suffix}", f"bob_{suffix}", "password123"
print(f"Тестирую {BASE}, пользователи {alice} и {bob}")

section("Этап 2: сервисы отвечают")
s, b = request("GET", "/api/hello")
check("api /api/hello -> 200", s == 200, b)
s, b = request("GET", "/auth/hello")
check("auth-service /auth/hello -> 200", s == 200, b)
s, b = request("GET", "/api/health")
check("api видит video-db, redis, rabbitmq, storage", s == 200, b)
s, b = request("GET", "/auth/health")
check("auth-service видит users-db", s == 200, b)

section("Аутентификация")
s, b = request("POST", "/auth/register", {"username": alice, "password": password})
check("регистрация alice -> 201", s == 201, b)
s, b = request("POST", "/auth/register", {"username": bob, "password": password})
check("регистрация bob -> 201", s == 201, b)
s, b = request("POST", "/auth/register", {"username": alice, "password": password})
check("повторная регистрация -> 409", s == 409, b)
s, b = request("POST", "/auth/register", {"username": "x", "password": "123"})
check("слишком короткие логин и пароль -> 422", s == 422, b)
s, b = request("POST", "/auth/login", {"username": alice, "password": "wrong-password"})
check("неверный пароль -> 401", s == 401, b)
_, b = request("POST", "/auth/login", {"username": alice, "password": password})
ta = b["access_token"]
_, b = request("POST", "/auth/login", {"username": bob, "password": password})
tb = b["access_token"]
check("вход выдаёт JWT", ta.count(".") == 2)
s, b = request("GET", "/api/me", token=ta[:-5] + "AAAAA")
check("поддельный JWT -> 401", s == 401, b)
s, b = request("GET", "/api/me")
check("без токена -> 401", s == 401, b)

section("Загрузка и обработка видео (PostgreSQL INSERT, S3, RabbitMQ)")
body, hdrs = multipart({"title": "Не видео"}, "file", "a.txt", b"hello", "text/plain")
s, b = request("POST", "/api/videos", body, token=ta, headers=hdrs)
check("загрузка не видео -> 415", s == 415, b)
body, hdrs = multipart({"title": "Автотест", "description": "Загружено автотестом"}, "file", "sample.mp4",
                       SAMPLE.read_bytes(), "video/mp4")
s, b = request("POST", "/api/videos", body, token=ta, headers=hdrs)
check("загрузка видео -> 201, статус processing", s == 201 and b["status"] == "processing", b)
vid = b["id"]
for _ in range(30):
    _, v = request("GET", f"/api/videos/{vid}")
    if v["status"] != "processing":
        break
    time.sleep(1)
check("video-worker обработал видео -> ready", v["status"] == "ready", v)
s, content, h = request("GET", f"/api/videos/{vid}/thumbnail", raw=True)
check("превью -> 200, image/jpeg", s == 200 and h.get("Content-Type") == "image/jpeg")
s, content, h = request("GET", f"/api/videos/{vid}/stream", headers={"Range": "bytes=0-99"}, raw=True)
check("видео с Range -> 206, 100 байт", s == 206 and len(content) == 100, s)

section("Просмотры (Redis)")
_, b1 = request("POST", f"/api/videos/{vid}/view")
_, b2 = request("POST", f"/api/videos/{vid}/view")
check("счётчик просмотров растёт", b2["views"] == b1["views"] + 1, (b1, b2))

section("Репутация (PostgreSQL + кеш Redis)")
s, b = request("POST", f"/api/videos/{vid}/vote", {"value": 1}, token=tb)
check("лайк от bob -> репутация 1", s == 200 and b["reputation"] == 1, b)
s, b = request("POST", f"/api/videos/{vid}/vote", {"value": -1}, token=tb)
check("bob меняет на дизлайк -> репутация -1 (UPDATE)", b["reputation"] == -1, b)
s, b = request("POST", f"/api/videos/{vid}/vote", {"value": 0}, token=tb)
check("bob снимает оценку -> репутация 0 (DELETE)", b["reputation"] == 0, b)
request("POST", f"/api/videos/{vid}/vote", {"value": 1}, token=tb)
s, b = request("POST", f"/api/videos/{vid}/vote", {"value": 1}, token=ta)
check("два лайка -> репутация видео 2", b["reputation"] == 2, b)
_, me = request("GET", "/api/me", token=ta)
check("репутация канала alice = сумма по её видео", me["reputation"] >= 2, me)

section("Рекомендации (кеш Redis)")
_, r1 = request("GET", "/api/recommendations")
_, r2 = request("GET", "/api/recommendations")
check("повторный запрос рекомендаций берётся из кеша (hit)", r2["cache"] == "hit", r2.get("cache"))

section("Права владельца (UPDATE / DELETE)")
s, b = request("PATCH", f"/api/videos/{vid}", {"title": "Взлом"}, token=tb)
check("bob меняет чужое видео -> 403", s == 403, b)
s, b = request("PATCH", f"/api/videos/{vid}", {"title": "Автотест (изменено)"}, token=ta)
check("alice меняет своё видео -> 200", s == 200 and b["title"] == "Автотест (изменено)", b)

section("Комментарии")
s, c = request("POST", f"/api/videos/{vid}/comments", {"text": "Комментарий <script>alert(1)</script>"}, token=tb)
check("bob оставляет комментарий -> 201", s == 201, c)
s, b = request("DELETE", f"/api/comments/{c['id']}", token=ta)
check("alice удаляет чужой комментарий -> 404", s == 404, b)
s, b = request("DELETE", f"/api/comments/{c['id']}", token=tb)
check("bob удаляет свой комментарий -> 204", s == 204, b)

section("Удаление видео")
s, b = request("DELETE", f"/api/videos/{vid}", token=tb)
check("bob удаляет чужое видео -> 403", s == 403, b)
s, b = request("DELETE", f"/api/videos/{vid}", token=ta)
check("alice удаляет своё видео -> 204", s == 204, b)
s, b = request("GET", f"/api/videos/{vid}")
check("удалённое видео -> 404", s == 404, b)

print(f"\nИтого: пройдено {passed}, ошибок {failed}")
sys.exit(1 if failed else 0)
