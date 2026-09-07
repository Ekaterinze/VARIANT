# -*- coding: utf-8 -*-
"""Работа с базой данных: листы голосования, пользователи, пароли, журнал.

Лист — это отдельное голосование за места в очереди: у него своё название,
описание, состав участников, дни недели и время приёма пожеланий. Листов может
быть несколько, в том числе несколько в один день.
"""

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
# поэтому время окон приёма всегда считаем в этом поясе.
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


# Расписание листа по умолчанию (такое же, как было до появления листов)
DEFAULT_OPEN_FROM = "20:00"
DEFAULT_OPEN_TO = "21:00"
DEFAULT_WEEKDAYS = "1234567"
DEFAULT_LIST_NAME = "Лабораторная работа"
DEFAULT_LIST_DESCRIPTION = ("Основная очередь сдачи лабораторной работы. "
                            "Выберите три желаемых места.")

# За сколько минут до открытия стираются прошлые пожелания листа
RESET_LEAD_MINUTES = 5

WEEKDAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

PASSWORD_LEN = 7
PASSWORD_ALPHABET = string.ascii_letters + string.digits
PASSWORD_RE = re.compile(r"^[A-Za-z0-9]{7}$")

# Стартовый пароль у всех одинаковый, дальше каждый меняет его сам.
DEFAULT_PASSWORD = os.environ.get("DEFAULT_PASSWORD") or "a123456"

SCHEMA_VERSION = "2"


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

CREATE TABLE IF NOT EXISTS lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    weekdays    TEXT NOT NULL DEFAULT '1234567',
    open_from   TEXT NOT NULL DEFAULT '20:00',
    open_to     TEXT NOT NULL DEFAULT '21:00',
    places      INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS list_members (
    list_id     INTEGER NOT NULL REFERENCES lists(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    PRIMARY KEY (list_id, user_id)
);

CREATE TABLE IF NOT EXISTS preferences (
    list_id     INTEGER NOT NULL,
    day         TEXT NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    p1          INTEGER NOT NULL,
    p2          INTEGER NOT NULL,
    p3          INTEGER NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (list_id, day, user_id)
);

CREATE TABLE IF NOT EXISTS results (
    list_id     INTEGER NOT NULL,
    day         TEXT NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    place       INTEGER NOT NULL,
    status      TEXT NOT NULL,
    rank        INTEGER,
    wishes      TEXT NOT NULL DEFAULT '',
    swapped     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (list_id, day, user_id)
);

CREATE TABLE IF NOT EXISTS swaps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id     INTEGER NOT NULL,
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
    list_id     INTEGER NOT NULL,
    day         TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (list_id, day)
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

WORK_TABLES = ["preferences", "results", "swaps", "report_meta"]


def pg_schema():
    """Та же схема на диалекте Postgres."""
    return SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


def table_columns(conn, table):
    """Имена колонок таблицы (пусто, если таблицы нет)."""
    if USE_POSTGRES:
        rows = conn.execute(
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_name = ?", (table,)).fetchall()
        return {r["name"] for r in rows}
    try:
        rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    except sqlite3.Error:
        return set()
    return {r["name"] for r in rows}


def init_schema(conn):
    conn.executescript(pg_schema() if USE_POSTGRES else SCHEMA)
    conn.commit()
    migrate(conn)


def migrate(conn):
    """Приводит старую базу (без листов) к текущей схеме.

    Пожелания, распределения и обмены — рабочие данные одного дня, поэтому при
    переходе на листы они пересоздаются. Люди, настройки и журнал сохраняются.
    """
    if get_setting(conn, "schema_version") == SCHEMA_VERSION:
        return False
    if "list_id" not in table_columns(conn, "preferences"):
        for table in WORK_TABLES:
            conn.execute("DROP TABLE IF EXISTS %s" % table)
        conn.commit()
        conn.executescript(pg_schema() if USE_POSTGRES else SCHEMA)
        conn.commit()
        log(conn, "СИСТЕМА", "MIGRATE",
            "база переведена на листы голосования: рабочие таблицы (%s) пересозданы"
            % ", ".join(WORK_TABLES), level="WARNING")
    set_setting(conn, "schema_version", SCHEMA_VERSION)
    return True


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


def test_mode(conn):
    """Режим отладки: приём пожеланий открыт круглосуточно."""
    return get_setting(conn, "test_mode", "0") == "1"


# --------------------------------------------------------------------- листы

def parse_hhmm(text, fallback="20:00"):
    match = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", str(text or ""))
    if not match:
        text = fallback
        match = re.match(r"^(\d{1,2}):(\d{2})$", fallback)
    hour, minute = int(match.group(1)), int(match.group(2))
    hour = min(max(hour, 0), 23)
    minute = min(max(minute, 0), 59)
    return time(hour, minute)


def clean_weekdays(text):
    """Оставляет только цифры 1..7 (1 — понедельник), по возрастанию, без повторов."""
    digits = sorted({c for c in str(text or "") if c in "1234567"})
    return "".join(digits) or DEFAULT_WEEKDAYS


def schedule_text(lst):
    """Расписание листа человеческим языком."""
    days = clean_weekdays(lst["weekdays"])
    if days == DEFAULT_WEEKDAYS:
        names = "ежедневно"
    else:
        names = "по " + ", ".join(WEEKDAY_SHORT[int(d) - 1] for d in days)
    return "%s с %s до %s" % (names, lst["open_from"], lst["open_to"])


def list_places(conn, lst):
    """Сколько мест в очереди листа: задано администратором или по числу людей."""
    if lst["places"] and int(lst["places"]) > 0:
        return int(lst["places"])
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM list_members WHERE list_id = ?", (lst["id"],)).fetchone()
    return row["c"] if row else 0


def is_scheduled_on(lst, day_date):
    return str(day_date.isoweekday()) in clean_weekdays(lst["weekdays"])


def list_window(conn, lst, now=None):
    """Состояние приёма пожеланий листа: ('open'|'closed', пояснение)."""
    now = now or local_now()
    if not lst["active"]:
        return "closed", "Лист выключен администратором."
    if test_mode(conn):
        return "open", "Режим отладки: приём пожеланий открыт круглосуточно."

    opens = parse_hhmm(lst["open_from"], DEFAULT_OPEN_FROM)
    closes = parse_hhmm(lst["open_to"], DEFAULT_OPEN_TO)
    today = is_scheduled_on(lst, now.date())

    if today and opens <= now.time() < closes:
        left = (datetime.combine(now.date(), closes) - now).seconds // 60
        return "open", "Приём пожеланий открыт, осталось примерно %d мин." % max(left, 0)

    nxt = next_open(lst, now)
    if nxt is None:
        return "closed", "Дни приёма не заданы."
    if nxt.date() == now.date():
        return "closed", "Приём пожеланий откроется сегодня в %s." % lst["open_from"]
    if nxt.date() == now.date() + timedelta(days=1):
        return "closed", "Приём пожеланий откроется завтра в %s." % lst["open_from"]
    return "closed", "Приём пожеланий откроется %s (%s) в %s." % (
        nxt.strftime("%d.%m"), WEEKDAY_SHORT[nxt.isoweekday() - 1], lst["open_from"])


def next_open(lst, now=None):
    """Ближайший момент открытия приёма (None, если дни не заданы)."""
    now = now or local_now()
    opens = parse_hhmm(lst["open_from"], DEFAULT_OPEN_FROM)
    for shift in range(0, 15):
        day = now.date() + timedelta(days=shift)
        if not is_scheduled_on(lst, day):
            continue
        moment = datetime.combine(day, opens)
        if moment > now:
            return moment
    return None


def seconds_to_open(lst, now=None):
    """Сколько секунд до открытия приёма (0, если уже открыт)."""
    now = now or local_now()
    opens = parse_hhmm(lst["open_from"], DEFAULT_OPEN_FROM)
    closes = parse_hhmm(lst["open_to"], DEFAULT_OPEN_TO)
    if is_scheduled_on(lst, now.date()) and opens <= now.time() < closes:
        return 0
    moment = next_open(lst, now)
    return int((moment - now).total_seconds()) if moment else 0


def create_list(conn, name, description="", weekdays=DEFAULT_WEEKDAYS,
                open_from=DEFAULT_OPEN_FROM, open_to=DEFAULT_OPEN_TO,
                places=0, active=1, member_ids=None):
    sql = ("INSERT INTO lists(name, description, weekdays, open_from, open_to,"
           " places, active, sort_order, created_at) VALUES(?,?,?,?,?,?,?,?,?)")
    params = (name.strip(), (description or "").strip(), clean_weekdays(weekdays),
              parse_hhmm(open_from, DEFAULT_OPEN_FROM).strftime("%H:%M"),
              parse_hhmm(open_to, DEFAULT_OPEN_TO).strftime("%H:%M"),
              int(places or 0), 1 if active else 0, 0,
              local_now().strftime("%Y-%m-%d %H:%M:%S"))
    if USE_POSTGRES:
        list_id = conn.execute(sql + " RETURNING id", params).fetchone()["id"]
    else:
        list_id = conn.execute(sql, params).lastrowid
    conn.commit()
    if member_ids is not None:
        set_list_members(conn, list_id, member_ids)
    return list_id


def update_list(conn, list_id, name, description, weekdays, open_from, open_to,
                places, active):
    conn.execute(
        "UPDATE lists SET name = ?, description = ?, weekdays = ?, open_from = ?,"
        " open_to = ?, places = ?, active = ? WHERE id = ?",
        (name.strip(), (description or "").strip(), clean_weekdays(weekdays),
         parse_hhmm(open_from, DEFAULT_OPEN_FROM).strftime("%H:%M"),
         parse_hhmm(open_to, DEFAULT_OPEN_TO).strftime("%H:%M"),
         int(places or 0), 1 if active else 0, list_id),
    )
    conn.commit()


def delete_list(conn, list_id):
    for table in WORK_TABLES:
        conn.execute("DELETE FROM %s WHERE list_id = ?" % table, (list_id,))
    conn.execute("DELETE FROM list_members WHERE list_id = ?", (list_id,))
    conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))
    conn.commit()


def get_list(conn, list_id):
    return conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()


def all_lists(conn):
    return conn.execute("SELECT * FROM lists ORDER BY open_from, name").fetchall()


def user_lists(conn, user_id):
    """Листы, в которых состоит человек, по времени открытия приёма."""
    return conn.execute(
        "SELECT l.* FROM lists l JOIN list_members m ON m.list_id = l.id "
        "WHERE m.user_id = ? ORDER BY l.open_from, l.name",
        (user_id,),
    ).fetchall()


def list_member_rows(conn, list_id):
    return conn.execute(
        "SELECT u.* FROM list_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.list_id = ? ORDER BY u.surname, u.name",
        (list_id,),
    ).fetchall()


def list_member_ids(conn, list_id):
    return [r["user_id"] for r in conn.execute(
        "SELECT user_id FROM list_members WHERE list_id = ?", (list_id,)).fetchall()]


def set_list_members(conn, list_id, user_ids):
    conn.execute("DELETE FROM list_members WHERE list_id = ?", (list_id,))
    rows = [(list_id, int(uid)) for uid in dict.fromkeys(user_ids or [])]
    if rows:
        conn.executemany("INSERT INTO list_members(list_id, user_id) VALUES(?,?)", rows)
    conn.commit()


def is_member(conn, list_id, user_id):
    return conn.execute(
        "SELECT 1 AS x FROM list_members WHERE list_id = ? AND user_id = ?",
        (list_id, user_id)).fetchone() is not None


def ensure_default_list(conn):
    """Создаёт лист по умолчанию со всеми людьми — один раз за жизнь базы."""
    if get_setting(conn, "default_list_created") == "1":
        return None
    if conn.execute("SELECT COUNT(*) AS c FROM lists").fetchone()["c"]:
        set_setting(conn, "default_list_created", "1")
        return None
    users = all_users(conn)
    if not users:
        return None
    list_id = create_list(conn, DEFAULT_LIST_NAME, DEFAULT_LIST_DESCRIPTION,
                          DEFAULT_WEEKDAYS, DEFAULT_OPEN_FROM, DEFAULT_OPEN_TO,
                          0, 1, [u["id"] for u in users])
    set_setting(conn, "default_list_created", "1")
    log(conn, "СИСТЕМА", "LIST_CREATE",
        "создан лист по умолчанию «%s» (%s), участников: %d"
        % (DEFAULT_LIST_NAME, DEFAULT_WEEKDAYS, len(users)))
    return list_id


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
    """Заводит пользователей из файла и выдаёт каждому стартовый пароль."""
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
    """Заводит людей и лист по умолчанию при первом запуске."""
    if not os.path.exists(PEOPLE_FILE):
        return []
    created = import_people(conn)
    if created:
        log(conn, "СИСТЕМА", "SEED",
            "база была пуста: заведено пользователей %d, стартовый пароль у всех одинаковый"
            % len(created))
    ensure_default_list(conn)
    return created


def get_user_by_login(conn, text):
    return conn.execute(
        "SELECT * FROM users WHERE login_key = ?", (login_key(text),)).fetchone()


def get_user(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def all_users(conn):
    return conn.execute("SELECT * FROM users ORDER BY surname, name").fetchall()


# ------------------------------------------------------------- пожелания

def get_preference(conn, list_id, day, user_id):
    return conn.execute(
        "SELECT * FROM preferences WHERE list_id = ? AND day = ? AND user_id = ?",
        (list_id, day, user_id)).fetchone()


def save_preference(conn, list_id, day, user_id, p1, p2, p3):
    conn.execute(
        "INSERT INTO preferences(list_id, day, user_id, p1, p2, p3, updated_at)"
        " VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(list_id, day, user_id) DO UPDATE SET p1=excluded.p1,"
        " p2=excluded.p2, p3=excluded.p3, updated_at=excluded.updated_at",
        (list_id, day, user_id, p1, p2, p3, local_now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()


def delete_preference(conn, list_id, day, user_id):
    conn.execute("DELETE FROM preferences WHERE list_id = ? AND day = ? AND user_id = ?",
                 (list_id, day, user_id))
    conn.commit()


def all_preferences(conn, list_id, day):
    return conn.execute(
        "SELECT p.*, u.full_name FROM preferences p JOIN users u ON u.id = p.user_id "
        "WHERE p.list_id = ? AND p.day = ? ORDER BY u.surname",
        (list_id, day),
    ).fetchall()


# ------------------------------------------------------------- результаты

def save_results(conn, list_id, day, rows, created_by, summary):
    conn.execute("DELETE FROM results WHERE list_id = ? AND day = ?", (list_id, day))
    conn.executemany(
        "INSERT INTO results(list_id, day, user_id, place, status, rank, wishes)"
        " VALUES(?,?,?,?,?,?,?)", rows)
    conn.execute(
        "INSERT INTO report_meta(list_id, day, created_at, created_by, summary)"
        " VALUES(?,?,?,?,?) "
        "ON CONFLICT(list_id, day) DO UPDATE SET created_at=excluded.created_at, "
        "created_by=excluded.created_by, summary=excluded.summary",
        (list_id, day, local_now().strftime("%Y-%m-%d %H:%M:%S"), created_by, summary),
    )
    conn.commit()


def get_results(conn, list_id, day):
    return conn.execute(
        "SELECT r.*, u.surname, u.name, u.patronymic, u.full_name "
        "FROM results r JOIN users u ON u.id = r.user_id "
        "WHERE r.list_id = ? AND r.day = ? ORDER BY r.place",
        (list_id, day),
    ).fetchall()


def get_result(conn, list_id, day, user_id):
    return conn.execute(
        "SELECT * FROM results WHERE list_id = ? AND day = ? AND user_id = ?",
        (list_id, day, user_id)).fetchone()


def get_report_meta(conn, list_id, day):
    return conn.execute("SELECT * FROM report_meta WHERE list_id = ? AND day = ?",
                        (list_id, day)).fetchone()


def report_days(conn, list_id):
    return [r["day"] for r in conn.execute(
        "SELECT day FROM report_meta WHERE list_id = ? ORDER BY day DESC",
        (list_id,)).fetchall()]


def latest_report_day(conn, list_id):
    row = conn.execute(
        "SELECT day FROM report_meta WHERE list_id = ? ORDER BY day DESC LIMIT 1",
        (list_id,)).fetchone()
    return row["day"] if row else None


def all_report_meta(conn):
    return conn.execute(
        "SELECT m.*, l.name AS list_name FROM report_meta m "
        "JOIN lists l ON l.id = m.list_id ORDER BY m.day DESC, l.name").fetchall()


# ------------------------------------------------- подготовка к новому приёму

def reset_list(conn, lst, actor, reason, include_today=False):
    """Стирает пожелания листа и отменяет его незакрытые обмены.

    include_today=False (автоматическая подготовка) — трогает только прошлые
    круги. Это важно: если сервер был выключен и «догоняет» подготовку уже
    после открытия приёма, он не должен стереть только что поданные пожелания.
    include_today=True — явное действие администратора «подготовить заново».
    """
    today = local_now().strftime("%Y-%m-%d")
    if include_today:
        where, prefixed, params = "list_id = ?", "p.list_id = ?", (lst["id"],)
    else:
        where = "list_id = ? AND day < ?"
        prefixed = "p.list_id = ? AND p.day < ?"
        params = (lst["id"], today)

    rows = conn.execute(
        "SELECT p.day, p.p1, p.p2, p.p3, u.full_name FROM preferences p "
        "JOIN users u ON u.id = p.user_id WHERE " + prefixed
        + " ORDER BY p.day, u.surname", params).fetchall()
    for r in rows:
        log(conn, "СИСТЕМА", "RESET_ARCHIVE",
            "«%s»: стёрты пожелания за %s: %s -> %d, %d, %d"
            % (lst["name"], r["day"], r["full_name"], r["p1"], r["p2"], r["p3"]))
    conn.execute("DELETE FROM preferences WHERE " + where, params)
    cur = conn.execute(
        "UPDATE swaps SET status = 'expired', resolved_at = ? "
        "WHERE " + where + " AND status = 'pending'",
        (local_now().strftime("%Y-%m-%d %H:%M:%S"),) + params)
    expired = max(cur.rowcount, 0)
    conn.commit()
    set_setting(conn, "last_reset_%d" % lst["id"], local_now().strftime("%Y-%m-%d"))
    log(conn, actor, "RESET",
        "«%s» — %s: стёрто пожеланий %d, отменено необработанных обменов %d"
        % (lst["name"], reason, len(rows), expired), level="WARNING")
    return len(rows), expired


def reset_all_lists(conn, actor, reason="ручная подготовка к новому расчёту"):
    prefs = swaps = 0
    for lst in all_lists(conn):
        a, b = reset_list(conn, lst, actor, reason, include_today=True)
        prefs += a
        swaps += b
    return prefs, swaps


def list_reset_moment(lst, day_date):
    """Момент подготовки листа — за RESET_LEAD_MINUTES до открытия приёма."""
    opens = parse_hhmm(lst["open_from"], DEFAULT_OPEN_FROM)
    return datetime.combine(day_date, opens) - timedelta(minutes=RESET_LEAD_MINUTES)


def ensure_list_resets(conn, now=None):
    """Готовит к приёму те листы, у которых сегодня подошло время.

    Срабатывает за 5 минут до открытия приёма и ровно один раз в день на лист —
    даже если сервер в этот момент был выключен и включился позже.
    """
    now = now or local_now()
    today = now.strftime("%Y-%m-%d")
    done = []
    for lst in all_lists(conn):
        if not lst["active"] or not is_scheduled_on(lst, now.date()):
            continue
        if now < list_reset_moment(lst, now.date()):
            continue
        if get_setting(conn, "last_reset_%d" % lst["id"]) == today:
            continue
        reset_list(conn, lst, "СИСТЕМА",
                   "автоматическая подготовка за %d мин до открытия приёма"
                   % RESET_LEAD_MINUTES)
        done.append(lst["name"])
    return done


def seconds_to_next_reset(conn, now=None):
    """Сколько секунд до ближайшей подготовки (для фонового планировщика)."""
    now = now or local_now()
    best = None
    for lst in all_lists(conn):
        if not lst["active"]:
            continue
        moment = next_open(lst, now)
        if moment is None:
            continue
        point = moment - timedelta(minutes=RESET_LEAD_MINUTES)
        if point <= now:
            return 0
        best = point if best is None else min(best, point)
    return int((best - now).total_seconds()) if best else 3600


# ------------------------------------------------------------- обмен местами

def create_swap(conn, list_id, day, from_user, to_user, from_place, to_place):
    sql = ("INSERT INTO swaps(list_id, day, from_user, to_user, from_place, to_place,"
           " status, created_at) VALUES(?,?,?,?,?,?, 'pending', ?)")
    params = (list_id, day, from_user, to_user, from_place, to_place,
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


def swaps_for_user(conn, list_id, day, user_id):
    return conn.execute(
        "SELECT s.*, uf.full_name AS from_name, ut.full_name AS to_name "
        "FROM swaps s JOIN users uf ON uf.id = s.from_user "
        "JOIN users ut ON ut.id = s.to_user "
        "WHERE s.list_id = ? AND s.day = ? AND (s.from_user = ? OR s.to_user = ?) "
        "ORDER BY s.id DESC",
        (list_id, day, user_id, user_id),
    ).fetchall()


def swaps_for_day(conn, list_id, day):
    return conn.execute(
        "SELECT s.*, uf.full_name AS from_name, ut.full_name AS to_name "
        "FROM swaps s JOIN users uf ON uf.id = s.from_user "
        "JOIN users ut ON ut.id = s.to_user "
        "WHERE s.list_id = ? AND s.day = ? ORDER BY s.id",
        (list_id, day),
    ).fetchall()


def pending_swap_between(conn, list_id, day, a, b):
    return conn.execute(
        "SELECT * FROM swaps WHERE list_id = ? AND day = ? AND status = 'pending' AND "
        "((from_user = ? AND to_user = ?) OR (from_user = ? AND to_user = ?))",
        (list_id, day, a, b, b, a),
    ).fetchone()


def set_swap_status(conn, swap_id, status):
    conn.execute("UPDATE swaps SET status = ?, resolved_at = ? WHERE id = ?",
                 (status, local_now().strftime("%Y-%m-%d %H:%M:%S"), swap_id))
    conn.commit()


def apply_swap(conn, list_id, day, user_a, place_a, user_b, place_b):
    """Меняет местами двух участников в готовом распределении листа."""
    conn.execute(
        "UPDATE results SET place = ?, swapped = 1 "
        "WHERE list_id = ? AND day = ? AND user_id = ?", (place_b, list_id, day, user_a))
    conn.execute(
        "UPDATE results SET place = ?, swapped = 1 "
        "WHERE list_id = ? AND day = ? AND user_id = ?", (place_a, list_id, day, user_b))
    # Остальные заявки этих двух людей становятся неактуальными.
    conn.execute(
        "UPDATE swaps SET status = 'expired', resolved_at = ? WHERE list_id = ? AND "
        "day = ? AND status = 'pending' AND (from_user IN (?, ?) OR to_user IN (?, ?))",
        (local_now().strftime("%Y-%m-%d %H:%M:%S"), list_id, day,
         user_a, user_b, user_a, user_b),
    )
    conn.commit()
