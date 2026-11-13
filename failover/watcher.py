"""Сторож отказоустойчивости PostgreSQL (video-db).

Каждые 2 секунды проверяет primary и standby.
- Если primary не отвечает 3 проверки подряд (около 6 с), а standby жив и в режиме
  восстановления, сторож выполняет на standby SELECT pg_promote() — standby становится master.
- Если после переключения старый primary снова поднялся и принимает запись, сторож
  переводит его в режим только чтения (защита от split-brain: двух master одновременно).

Приложения подключаются строкой host=video-db,video-db-standby target_session_attrs=read-write,
поэтому драйвер сам находит узел, куда можно писать.

Команда статуса:  docker compose exec failover python watcher.py status
"""
import os
import sys
import time
from datetime import datetime

import psycopg

NODES = ["video-db", "video-db-standby"]
AUTH = f"dbname={os.environ['VIDEO_DB_NAME']} user={os.environ['VIDEO_DB_USER']} " \
       f"password={os.environ['VIDEO_DB_PASSWORD']} connect_timeout=2"
CHECK_INTERVAL = 2
FAILS_BEFORE_PROMOTE = 3


def log(msg):
    print(f"{datetime.now():%H:%M:%S} failover: {msg}", flush=True)


def state(host):
    """'primary' | 'standby' | 'read-only' (огороженный бывший primary) | 'down'."""
    try:
        with psycopg.connect(f"host={host} {AUTH}", autocommit=True) as conn:
            in_recovery = conn.execute("SELECT pg_is_in_recovery()").fetchone()[0]
            if in_recovery:
                return "standby"
            ro = conn.execute("SHOW default_transaction_read_only").fetchone()[0]
            return "read-only" if ro == "on" else "primary"
    except psycopg.Error:
        return "down"


def promote(host):
    with psycopg.connect(f"host={host} {AUTH}", autocommit=True) as conn:
        conn.execute("SELECT pg_promote(true, 30)")


def fence(host):
    """Бывший primary вернулся: запрещаем запись и обрываем его клиентов."""
    with psycopg.connect(f"host={host} {AUTH}", autocommit=True) as conn:
        conn.execute("ALTER SYSTEM SET default_transaction_read_only = on")
        conn.execute("SELECT pg_reload_conf()")
        conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                     "WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'")


def status():
    for host in NODES:
        print(f"{host:18} {state(host)}")
    for host in NODES:
        try:
            with psycopg.connect(f"host={host} {AUTH}", autocommit=True) as conn:
                rows = conn.execute("SELECT client_addr, state, sync_state, "
                                    "pg_wal_lsn_diff(sent_lsn, replay_lsn) AS lag_bytes "
                                    "FROM pg_stat_replication").fetchall()
                for r in rows:
                    print(f"репликация {host} -> {r[0]}: {r[1]}, {r[2]}, отставание {r[3]} байт")
        except psycopg.Error:
            pass


def watch():
    fails = 0
    last = None
    log(f"слежу за {NODES[0]} (primary) и {NODES[1]} (standby)")
    while True:
        a, b = state(NODES[0]), state(NODES[1])
        if (a, b) != last:
            log(f"{NODES[0]}={a}, {NODES[1]}={b}")
            last = (a, b)
        primaries = [h for h, s in zip(NODES, (a, b)) if s == "primary"]

        if not primaries:
            standbys = [h for h, s in zip(NODES, (a, b)) if s == "standby"]
            fails += 1
            if fails >= FAILS_BEFORE_PROMOTE and standbys:
                t0 = time.time()
                log(f"primary недоступен {fails * CHECK_INTERVAL} с — продвигаю {standbys[0]} в master")
                try:
                    promote(standbys[0])
                    log(f"{standbys[0]} стал master за {time.time() - t0:.1f} с")
                except psycopg.Error as exc:
                    log(f"не удалось продвинуть {standbys[0]}: {exc}")
                fails = 0
        else:
            fails = 0

        if len(primaries) == 2:
            # Оба принимают запись: огораживаем исходный primary, master теперь бывший standby
            log(f"{NODES[0]} вернулся после переключения — перевожу его в режим только чтения (split-brain)")
            try:
                fence(NODES[0])
            except psycopg.Error as exc:
                log(f"не удалось оградить {NODES[0]}: {exc}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    status() if sys.argv[1:] == ["status"] else watch()
