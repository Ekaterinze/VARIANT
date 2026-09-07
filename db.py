# -*- coding: utf-8 -*-
"""Работа с базой данных, пользователями, паролями и журналом событий."""

import os
import re
import sqlite3
import hashlib
import secrets
import string
from datetime import datetime, time, timedelta

try:                                   # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:                    # pragma: no cover
    ZoneInfo = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "data")
LOG_DIR = os.environ.get("LOG_DIR") or os.path.join(BASE_DIR, "logs")
DB_PATH = os.environ.get("DB_PATH") or os.path.join(DATA_DIR, "lab.db")
PEOPLE_FILE = os.environ.get("PEOPLE_FILE") or os.path.join(BASE_DIR, "Люди.txt")
PASSWORDS_FILE = os.path.join(DATA_DIR, "Пароли.txt")

ADMIN_FULL_NAME = os.environ.get("ADMIN_FULL_NAME") or "Пархачева Екатерина Евгеньевна"

# Часовой пояс расписания. На хостинге (Render и т.п.) система живёт по UTC,
# поэтому время окна 20:00-21:00 всегда считаем в этом поясе.
TZ_NAME = os.environ.get("APP_TZ") or "Europe/Moscow"
try:
    TZ = ZoneInfo(TZ_NAME) if ZoneInfo else None
except Exception:                      # нет базы часовых поясов — берём системное время
    TZ = None


def local_now():
    """Текущее время в часовом поясе расписания (наивный datetime)."""
    if TZ is None:
        return datetime.now()
    return datetime.now(TZ).replace(tzinfo=None)

# Окно доступности сайта для обычных пользователей
OPEN_FROM = time(20, 0)
OPEN_TO = time(21, 0)
# За пять минут до открытия старые пожелания стираются, сайт готов к новому дню
RESET_AT = time(19, 55)

PASSWORD_LEN = 7
PASSWORD_ALPHABET = string.ascii_letters + string.digits
PASSWORD_RE = re.compile(r"^[A-Za-z0-9]{7}$")

# Стартовый пароль у всех одинаковый, дальше каждый меняет его сам.
DEFAULT_PASSWORD = os.environ.get("DEFAULT_PASSWORD") or "a123456"


# ---------------------------------------------------------------- соединение

# Если задана переменная DATABASE_URL (например, бесплатная база Neon.tech или
# Postgres на Render), данные хранятся в Postgres и переживают перезапуск
# сервиса. Без неё используется локальный файл SQLite.
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
USE_POSTGRES = DATABASE_URL.startswith("postgres")


def _pg_sql(sql):
    """Переводит запрос из диалекта SQLite в Postgres: ? -> %s.

    В наших запросах знак вопроса встречается только как место подстановки,
    внутри текстовых констант его нет.
    """
    return sql.replace("?", "%s")


class PgConnection:
    """Обёртка над psycopg с интерфейсом sqlite3.Connection.

    Благодаря ей весь остальной код (запросы с «?») одинаково работает
    и с SQLite, и с Postgres.
    """

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        return self._raw.execute(_pg_sql(sql), tuple(params))

    def executemany(self, sql, rows):
        cur = self._raw.cursor()
        cur.executemany(_pg_sql(sql), [tuple(r) for r in rows])
        return cur

    def executescript(self, script):
        cur = self._raw.cursor()
        cur.execute(script)
        return cur

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()


def connect():
    if USE_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row
        # connect_timeout: если база недоступна, запрос не должен висеть вечно
        return PgConnection(psycopg.connect(
            DATABASE_URL, row_factory=dict_row,
            connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "10"))))
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    surname     TEXT NOT NULL,
    name        TEXT NOT NULL,
    patronymic  TEXT NOT NULL DEFAULT '',
    full_name   TEXT NOT NULL,
    login_key   TEXT NOT NULL UNIQUE,
    pwd_hash    TEXT NOT NULL,
    pwd_salt    TEXT NOT NULL,
    is_admin    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS preferences (
    day         TEXT NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    p1          INTEGER NOT NULL,
    p2          INTEGER NOT NULL,
    p3          INTEGER NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (day, user_id)
);

CREATE TABLE IF NOT EXISTS results (
    day         TEXT NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    place       INTEGER NOT NULL,
    status      TEXT NOT NULL,
    rank        INTEGER,
    wishes      TEXT NOT NULL DEFAULT '',
    swapped     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id)
);

CREATE TABLE IF NOT EXISTS swaps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT NOT NULL,
    from_user   INTEGER NOT NULL REFERENCES users(id),
    to_user     INTEGER NOT NULL REFERENCES users(id),
    from_place  INTEGER NOT NULL,
    to_place    INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS report_meta (
    day         TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    day         TEXT NOT NULL,
    level       TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""


def pg_schema():
    """Та же схема на диалекте Postgres."""
    return SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


def init_schema(conn):
    conn.executescript(pg_schema() if USE_POSTGRES else SCHEMA)
    conn.commit()


# ------------------------------------------------------------------ настройки

def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def places_count(conn):
    """Количество мест в очереди сдачи. По умолчанию равно числу людей."""
    raw = get_setting(conn, "places_count")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw)
    row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    return row["c"] if row else 0


def test_mode(conn):
    """Режим отладки: сайт открыт для всех круглосуточно."""
    return get_setting(conn, "test_mode", "0") == "1"


# ---------------------------------------------------------------- расписание

def is_window_open(now=None):
    now = now or local_now()
    return OPEN_FROM <= now.time() < OPEN_TO


def window_state(conn, now=None):
    """Возвращает пару: состояние ('open'/'closed') и пояснение для экрана."""
    now = now or local_now()
    if test_mode(conn):
        return "open", "Режим отладки: доступ открыт круглосуточно."
    if is_window_open(now):
        left = (datetime.combine(now.date(), OPEN_TO) - now).seconds // 60
        return "open", "Приём пожеланий открыт, осталось примерно %d мин." % max(left, 0)
    if now.time() < OPEN_FROM:
        return "closed", "Приём пожеланий откроется сегодня в %s." % OPEN_FROM.strftime("%H:%M")
    return "closed", "Приём пожеланий на сегодня закрыт (работал с %s до %s)." % (
        OPEN_FROM.strftime("%H:%M"), OPEN_TO.strftime("%H:%M"))


def seconds_to_open(now=None):
    """Сколько секунд осталось до открытия окна (0, если уже открыто)."""
    now = now or local_now()
    if is_window_open(now):
        return 0
    today_open = datetime.combine(now.date(), OPEN_FROM)
    if now < today_open:
        return int((today_open - now).total_seconds())
    return int((today_open + timedelta(days=1) - now).total_seconds())


# ------------------------------------------------------------------- пароли

def generate_password():
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LEN))


def initial_password(key=None):
    """Стартовый пароль, одинаковый для всех: его участник меняет сам."""
    return DEFAULT_PASSWORD


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return digest.hex(), salt


def check_password(password, pwd_hash, salt):
    calc, _ = hash_password(password, salt)
    return secrets.compare_digest(calc, pwd_hash)


def set_user_password(conn, user_id, password):
    pwd_hash, salt = hash_password(password)
    conn.execute("UPDATE users SET pwd_hash = ?, pwd_salt = ? WHERE id = ?",
                 (pwd_hash, salt, user_id))
    conn.commit()


# ---------------------------------------------------------------- логин-ключ

def login_key(text):
    """Приводит введённое ФИО к ключу вида 'фамилия имя'."""
    parts = [p for p in re.split(r"\s+", (text or "").strip()) if p]
    parts = [p.replace("ё", "е").replace("Ё", "Е").lower() for p in parts]
    return " ".join(parts[:2])


# ----------------------------------------------------------------- журнал

def log(conn, actor, action, message="", level="INFO", day=None):
    now = local_now()
    day = day or now.strftime("%Y-%m-%d")
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO events(ts, day, level, actor, action, message) VALUES(?,?,?,?,?,?)",
        (ts, day, level, actor, action, message),
    )
    conn.commit()
    os.makedirs(LOG_DIR, exist_ok=True)
    line = "%s | %-7s | %-30s | %-24s | %s\n" % (ts, level, actor, action, message)
    with open(os.path.join(LOG_DIR, "%s.log" % day), "a", encoding="utf-8") as fh:
        fh.write(line)


def read_events(conn, day_from, day_to):
    return conn.execute(
        "SELECT * FROM events WHERE day BETWEEN ? AND ? ORDER BY id",
        (day_from, day_to),
    ).fetchall()


# ------------------------------------------------------------- пользователи

def parse_people_file(path=PEOPLE_FILE):
    people = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            parts = re.split(r"\s+", line)
            surname = parts[0]
            name = parts[1] if len(parts) > 1 else ""
            patronymic = " ".join(parts[2:]) if len(parts) > 2 else ""
            people.append((surname, name, patronymic, " ".join(parts)))
    return people


def import_people(conn, path=PEOPLE_FILE):
    """Заводит пользователей из файла и выдаёт каждому пароль.

    Возвращает список пар (ФИО, пароль) для вновь созданных пользователей.
    """
    created = []
    for surname, name, patronymic, full_name in parse_people_file(path):
        key = login_key(full_name)
        if conn.execute("SELECT id FROM users WHERE login_key = ?", (key,)).fetchone():
            continue
        password = initial_password(key)
        pwd_hash, salt = hash_password(password)
        is_admin = 1 if key == login_key(ADMIN_FULL_NAME) else 0
        conn.execute(
            "INSERT INTO users(surname, name, patronymic, full_name, login_key,"
            " pwd_hash, pwd_salt, is_admin) VALUES(?,?,?,?,?,?,?,?)",
            (surname, name, patronymic, full_name, key, pwd_hash, salt, is_admin),
        )
        created.append((full_name, password))
    conn.commit()
    return created


def ensure_seeded(conn):
    """Заводит людей при первом запуске (нужно на хостинге с чистым диском).

    Возвращает список пар (ФИО, пароль) — пустой, если все уже заведены.
    """
    if not os.path.exists(PEOPLE_FILE):
        return []
    created = import_people(conn)
    if created:
        log(conn, "СИСТЕМА", "SEED",
            "база была пуста: заведено пользователей %d, стартовый пароль у всех одинаковый"
            % len(created))
    return created


def get_user_by_login(conn, text):
    return conn.execute(
        "SELECT * FROM users WHERE login_key = ?", (login_key(text),)).fetchone()


def get_user(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def all_users(conn):
    return conn.execute("SELECT * FROM users ORDER BY surname, name").fetchall()


# ------------------------------------------------------------- пожелания

def get_preference(conn, day, user_id):
    return conn.execute(
        "SELECT * FROM preferences WHERE day = ? AND user_id = ?", (day, user_id)
    ).fetchone()


def save_preference(conn, day, user_id, p1, p2, p3):
    conn.execute(
        "INSERT INTO preferences(day, user_id, p1, p2, p3, updated_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(day, user_id) DO UPDATE SET p1=excluded.p1, p2=excluded.p2, "
        "p3=excluded.p3, updated_at=excluded.updated_at",
        (day, user_id, p1, p2, p3, local_now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()


def delete_preference(conn, day, user_id):
    conn.execute("DELETE FROM preferences WHERE day = ? AND user_id = ?", (day, user_id))
    conn.commit()


def all_preferences(conn, day):
    return conn.execute(
        "SELECT p.*, u.full_name FROM preferences p JOIN users u ON u.id = p.user_id "
        "WHERE p.day = ? ORDER BY u.surname",
        (day,),
    ).fetchall()


# ------------------------------------------------------------- результаты

def save_results(conn, day, rows, created_by, summary):
    conn.execute("DELETE FROM results WHERE day = ?", (day,))
    conn.executemany(
        "INSERT INTO results(day, user_id, place, status, rank, wishes) VALUES(?,?,?,?,?,?)",
        rows,
    )
    conn.execute(
        "INSERT INTO report_meta(day, created_at, created_by, summary) VALUES(?,?,?,?) "
        "ON CONFLICT(day) DO UPDATE SET created_at=excluded.created_at, "
        "created_by=excluded.created_by, summary=excluded.summary",
        (day, local_now().strftime("%Y-%m-%d %H:%M:%S"), created_by, summary),
    )
    conn.commit()


def get_results(conn, day):
    return conn.execute(
        "SELECT r.*, u.surname, u.name, u.patronymic, u.full_name "
        "FROM results r JOIN users u ON u.id = r.user_id "
        "WHERE r.day = ? ORDER BY r.place",
        (day,),
    ).fetchall()


def get_report_meta(conn, day):
    return conn.execute("SELECT * FROM report_meta WHERE day = ?", (day,)).fetchone()


def report_days(conn):
    return [r["day"] for r in conn.execute(
        "SELECT day FROM report_meta ORDER BY day DESC").fetchall()]


def latest_report_day(conn):
    row = conn.execute("SELECT day FROM report_meta ORDER BY day DESC LIMIT 1").fetchone()
    return row["day"] if row else None


def get_result(conn, day, user_id):
    return conn.execute(
        "SELECT * FROM results WHERE day = ? AND user_id = ?", (day, user_id)).fetchone()


# --------------------------------------------------- подготовка к новому дню

def reset_day(conn, actor, reason="ежедневная подготовка к новому расчёту"):
    """Стирает все пожелания и незакрытые обмены, готовя сайт к новому дню.

    Результаты прошлых расчётов и отчёты сохраняются — стираются только
    пожелания, чтобы каждый день люди выбирали места заново.
    """
    rows = conn.execute(
        "SELECT p.day, p.p1, p.p2, p.p3, u.full_name FROM preferences p "
        "JOIN users u ON u.id = p.user_id ORDER BY p.day, u.surname").fetchall()
    for r in rows:
        log(conn, "СИСТЕМА", "RESET_ARCHIVE",
            "стёрты пожелания за %s: %s -> %d, %d, %d"
            % (r["day"], r["full_name"], r["p1"], r["p2"], r["p3"]))
    conn.execute("DELETE FROM preferences")
    cur = conn.execute(
        "UPDATE swaps SET status = 'expired', resolved_at = ? WHERE status = 'pending'",
        (local_now().strftime("%Y-%m-%d %H:%M:%S"),))
    expired = cur.rowcount
    conn.commit()
    set_setting(conn, "last_reset", local_now().strftime("%Y-%m-%d"))
    log(conn, actor, "RESET",
        "%s: стёрто пожеланий %d, отменено необработанных обменов %d"
        % (reason, len(rows), max(expired, 0)), level="WARNING")
    return len(rows), max(expired, 0)


def ensure_daily_reset(conn, now=None):
    """Выполняет сброс, если сегодня уже наступило 19:55, а сброса ещё не было."""
    now = now or local_now()
    if now.time() < RESET_AT:
        return False
    today = now.strftime("%Y-%m-%d")
    if get_setting(conn, "last_reset") == today:
        return False
    reset_day(conn, "СИСТЕМА",
              "автоматическая подготовка в %s" % RESET_AT.strftime("%H:%M"))
    return True


def seconds_to_reset(now=None):
    now = now or local_now()
    point = datetime.combine(now.date(), RESET_AT)
    if now >= point:
        point += timedelta(days=1)
    return int((point - now).total_seconds())


# ------------------------------------------------------------- обмен местами

def create_swap(conn, day, from_user, to_user, from_place, to_place):
    sql = ("INSERT INTO swaps(day, from_user, to_user, from_place, to_place, status,"
           " created_at) VALUES(?,?,?,?,?, 'pending', ?)")
    params = (day, from_user, to_user, from_place, to_place,
              local_now().strftime("%Y-%m-%d %H:%M:%S"))
    if USE_POSTGRES:                       # у Postgres номер строки берём из RETURNING
        row = conn.execute(sql + " RETURNING id", params).fetchone()
        conn.commit()
        return row["id"]
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid


def get_swap(conn, swap_id):
    return conn.execute("SELECT * FROM swaps WHERE id = ?", (swap_id,)).fetchone()


def swaps_for_user(conn, day, user_id):
    return conn.execute(
        "SELECT s.*, uf.full_name AS from_name, ut.full_name AS to_name "
        "FROM swaps s JOIN users uf ON uf.id = s.from_user "
        "JOIN users ut ON ut.id = s.to_user "
        "WHERE s.day = ? AND (s.from_user = ? OR s.to_user = ?) ORDER BY s.id DESC",
        (day, user_id, user_id),
    ).fetchall()


def swaps_for_day(conn, day):
    return conn.execute(
        "SELECT s.*, uf.full_name AS from_name, ut.full_name AS to_name "
        "FROM swaps s JOIN users uf ON uf.id = s.from_user "
        "JOIN users ut ON ut.id = s.to_user WHERE s.day = ? ORDER BY s.id",
        (day,),
    ).fetchall()


def pending_swap_between(conn, day, a, b):
    return conn.execute(
        "SELECT * FROM swaps WHERE day = ? AND status = 'pending' AND "
        "((from_user = ? AND to_user = ?) OR (from_user = ? AND to_user = ?))",
        (day, a, b, b, a),
    ).fetchone()


def set_swap_status(conn, swap_id, status):
    conn.execute("UPDATE swaps SET status = ?, resolved_at = ? WHERE id = ?",
                 (status, local_now().strftime("%Y-%m-%d %H:%M:%S"), swap_id))
    conn.commit()


def apply_swap(conn, day, user_a, place_a, user_b, place_b):
    """Меняет местами двух участников в готовом распределении."""
    conn.execute("UPDATE results SET place = ?, swapped = 1 WHERE day = ? AND user_id = ?",
                 (place_b, day, user_a))
    conn.execute("UPDATE results SET place = ?, swapped = 1 WHERE day = ? AND user_id = ?",
                 (place_a, day, user_b))
    # Остальные заявки этих двух людей становятся неактуальными.
    conn.execute(
        "UPDATE swaps SET status = 'expired', resolved_at = ? WHERE day = ? AND "
        "status = 'pending' AND (from_user IN (?, ?) OR to_user IN (?, ?))",
        (local_now().strftime("%Y-%m-%d %H:%M:%S"), day, user_a, user_b, user_a, user_b),
    )
    conn.commit()
