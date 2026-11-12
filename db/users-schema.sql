-- Схема БД пользователей (users DB). Доступ к ней есть только у auth-service.
-- Выполняется автоматически при первом запуске контейнера users-db.

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(32)  NOT NULL UNIQUE,
    password_hash VARCHAR(100) NOT NULL,          -- bcrypt-хеш, пароль в открытом виде не хранится
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT username_format CHECK (username ~ '^[A-Za-z0-9_]{3,32}$')
);
