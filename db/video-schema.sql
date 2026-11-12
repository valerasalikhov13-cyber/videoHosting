-- Схема БД видео (video DB). Доступ есть у api и video-worker.
-- Выполняется автоматически при первом запуске контейнера video-db.

-- Канал. Один пользователь = один канал, id канала совпадает с id пользователя из auth-service.
CREATE TABLE IF NOT EXISTS channels (
    id         INTEGER PRIMARY KEY,
    name       VARCHAR(32) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Видео. Сам файл и превью лежат в S3-хранилище, здесь только метаданные.
CREATE TABLE IF NOT EXISTS videos (
    id            BIGSERIAL PRIMARY KEY,
    channel_id    INTEGER      NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    title         VARCHAR(200) NOT NULL,
    description   TEXT         NOT NULL DEFAULT '',
    status        VARCHAR(16)  NOT NULL DEFAULT 'processing'
                  CHECK (status IN ('processing', 'ready', 'failed')),
    object_key    VARCHAR(255) NOT NULL,          -- ключ видеофайла в S3
    thumbnail_key VARCHAR(255),                   -- ключ превью в S3, появляется после обработки
    views         BIGINT       NOT NULL DEFAULT 0,-- копия счётчика из Redis, сбрасывается раз в 30 с
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS videos_channel_idx ON videos(channel_id);
CREATE INDEX IF NOT EXISTS videos_created_idx ON videos(created_at DESC);

-- Оценки: один пользователь ставит видео не больше одной оценки (+1 лайк, -1 дизлайк).
CREATE TABLE IF NOT EXISTS votes (
    video_id   BIGINT   NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    user_id    INTEGER  NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    value      SMALLINT NOT NULL CHECK (value IN (-1, 1)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (video_id, user_id)
);

-- Комментарии.
CREATE TABLE IF NOT EXISTS comments (
    id         BIGSERIAL PRIMARY KEY,
    video_id   BIGINT      NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    author_id  INTEGER     NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    text       VARCHAR(2000) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS comments_video_idx ON comments(video_id, created_at);

-- Репутация видео = лайки − дизлайки.
CREATE OR REPLACE VIEW video_reputation AS
SELECT v.id AS video_id, v.channel_id, COALESCE(SUM(vo.value), 0)::INTEGER AS reputation
FROM videos v LEFT JOIN votes vo ON vo.video_id = v.id
GROUP BY v.id;

-- Репутация канала = сумма репутаций всех его видео.
CREATE OR REPLACE VIEW channel_reputation AS
SELECT c.id AS channel_id, COALESCE(SUM(vr.reputation), 0)::INTEGER AS reputation
FROM channels c LEFT JOIN video_reputation vr ON vr.channel_id = c.id
GROUP BY c.id;
