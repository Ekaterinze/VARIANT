# -*- coding: utf-8 -*-
"""Сайт выбора места (очереди) для сдачи лабораторной работы.

Backend: Flask + SQLite/Postgres, отдаёт JSON API и один HTML-каркас.
Frontend: Vue 3 (static/app.js), подключается без сборки.

Правила работы:
  * войти на сайт можно в любое время;
  * пожелания принимаются по расписанию листа, который завёл администратор
    (дни недели и время задаются для каждого листа отдельно);
  * за 5 минут до открытия приёма пожелания листа стираются;
  * после расчёта участники видят очередь целиком и могут меняться местами.
"""

import io
import os
import re
import csv
import time as time_mod
import zipfile
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, session, jsonify,
                   send_file, abort)

import db
import assignment
import report_import

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRET_FILE = os.path.join(db.DATA_DIR, "secret.key")

app = Flask(__name__)


def _secret_key():
    """Ключ подписи cookie: из переменной SECRET_KEY либо из файла рядом с базой."""
    from_env = os.environ.get("SECRET_KEY")
    if from_env:
        return from_env
    os.makedirs(db.DATA_DIR, exist_ok=True)
    if not os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_hex(32))
    with open(SECRET_FILE, "r", encoding="utf-8") as fh:
        return fh.read().strip()


app.secret_key = _secret_key()
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# На хостинге сайт работает по HTTPS — cookie стоит помечать как Secure.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SECURE_COOKIES", "0") == "1"
app.config["JSON_AS_ASCII"] = False

STATUS_TEXT = {
    "satisfied": "пожелание учтено",
    "missed": "пожелание не учтено (ближайшее возможное место)",
    "indifferent": "не голосовал, место назначено случайно",
}

SWAP_STATUS_TEXT = {
    "pending": "ожидает ответа",
    "accepted": "обмен состоялся",
    "declined": "отказ",
    "cancelled": "отозвана",
    "expired": "неактуальна",
}


# ------------------------------------------------------------- инфраструктура

def get_conn():
    conn = db.connect()
    db.init_schema(conn)
    return conn


def today_str():
    return db.local_now().strftime("%Y-%m-%d")


def current_user(conn):
    uid = session.get("user_id")
    return db.get_user(conn, uid) if uid else None


def fail(message, code=400):
    return jsonify({"error": message}), code


def api_login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        conn = get_conn()
        try:
            user = current_user(conn)
            if not user:
                return fail("Требуется вход на сайт.", 401)
            return view(conn, user, *args, **kwargs)
        finally:
            conn.close()
    return wrapper


def api_admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        conn = get_conn()
        try:
            user = current_user(conn)
            if not user:
                return fail("Требуется вход на сайт.", 401)
            if not user["is_admin"]:
                return fail("Раздел доступен только администратору.", 403)
            return view(conn, user, *args, **kwargs)
        finally:
            conn.close()
    return wrapper


_reset_checked_at = None


@app.before_request
def before_any_request():
    """Готовит листы к приёму, даже если сервер в нужный момент был выключен.

    Проверка не чаще раза в минуту на процесс — сами сбросы защищены отметкой
    в настройках и за день выполняются ровно один раз на лист.
    """
    global _reset_checked_at
    now = db.local_now()
    if _reset_checked_at and (now - _reset_checked_at).total_seconds() < 60:
        return
    _reset_checked_at = now
    conn = get_conn()
    try:
        db.ensure_list_resets(conn, now)
    finally:
        conn.close()


def user_payload(user):
    return {"id": user["id"], "full_name": user["full_name"],
            "surname": user["surname"], "name": user["name"],
            "is_admin": bool(user["is_admin"])}


def window_payload(conn, lst, now=None):
    state, hint = db.list_window(conn, lst, now)
    return {"state": state, "hint": hint,
            "seconds_to_open": db.seconds_to_open(lst, now)}


def list_brief(conn, lst, now=None):
    return {
        "id": lst["id"],
        "name": lst["name"],
        "description": lst["description"],
        "weekdays": lst["weekdays"],
        "open_from": lst["open_from"],
        "open_to": lst["open_to"],
        "active": bool(lst["active"]),
        "places": db.list_places(conn, lst),
        "places_setting": lst["places"],
        "schedule_text": db.schedule_text(lst),
        "scheduled_today": db.is_scheduled_on(lst, (now or db.local_now()).date()),
        "window": window_payload(conn, lst, now),
    }


# ------------------------------------------------------------------ каркас

@app.route("/")
def spa():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    """Проверка живости для хостинга (Render дергает этот адрес)."""
    info = {"time": db.local_now().strftime("%Y-%m-%d %H:%M:%S"),
            "tz": db.TZ_NAME,
            "storage": "postgres" if db.USE_POSTGRES else "sqlite"}
    try:
        conn = get_conn()
        try:
            info["users"] = len(db.all_users(conn))
            info["lists"] = len(db.all_lists(conn))
        finally:
            conn.close()
    except Exception as exc:                      # noqa: BLE001
        info["status"] = "error"
        info["error"] = "база недоступна: %s" % exc
        return jsonify(info), 503
    info["status"] = "ok"
    return jsonify(info)


# ------------------------------------------------------------------- сессия

@app.route("/api/session")
def api_session():
    conn = get_conn()
    try:
        user = current_user(conn)
        return jsonify({
            "user": user_payload(user) if user else None,
            "test_mode": db.test_mode(conn),
            "reset_lead": db.RESET_LEAD_MINUTES,
            "server_time": db.local_now().strftime("%d.%m.%Y %H:%M:%S"),
        })
    finally:
        conn.close()


@app.route("/api/login", methods=["POST"])
def api_login():
    conn = get_conn()
    try:
        data = request.get_json(silent=True) or {}
        fio = (data.get("fio") or "").strip()
        password = data.get("password") or ""
        user = db.get_user_by_login(conn, fio)
        if not user or not db.check_password(password, user["pwd_hash"], user["pwd_salt"]):
            db.log(conn, fio or "(пусто)", "LOGIN_FAIL",
                   "неверная фамилия/имя или пароль", level="WARNING")
            return fail("Неверная фамилия, имя или пароль.", 401)

        session["user_id"] = user["id"]
        db.log(conn, user["full_name"], "LOGIN",
               "вход выполнен%s" % (" (администратор)" if user["is_admin"] else ""))
        return jsonify({"user": user_payload(user)})
    finally:
        conn.close()


@app.route("/api/logout", methods=["POST"])
def api_logout():
    conn = get_conn()
    try:
        user = current_user(conn)
        if user:
            db.log(conn, user["full_name"], "LOGOUT", "выход")
    finally:
        conn.close()
    session.clear()
    return jsonify({"ok": True})


# --------------------------------------------------------- страница участника

def swap_payload(row, me_id):
    return {
        "id": row["id"], "day": row["day"], "status": row["status"],
        "status_text": SWAP_STATUS_TEXT.get(row["status"], row["status"]),
        "from_user": row["from_user"], "to_user": row["to_user"],
        "from_name": row["from_name"], "to_name": row["to_name"],
        "from_place": row["from_place"], "to_place": row["to_place"],
        "created_at": row["created_at"], "resolved_at": row["resolved_at"],
        "direction": "outgoing" if row["from_user"] == me_id else "incoming",
    }


def list_state_for_user(conn, lst, user, day, now):
    """Всё, что участник видит про один лист."""
    info = list_brief(conn, lst, now)
    info["can_edit"] = info["window"]["state"] == "open"

    pref = db.get_preference(conn, lst["id"], day, user["id"])
    info["pref"] = ({"p1": pref["p1"], "p2": pref["p2"], "p3": pref["p3"],
                     "updated_at": pref["updated_at"]} if pref else None)

    members = db.list_member_rows(conn, lst["id"])
    voted = db.all_preferences(conn, lst["id"], day)
    voted_ids = {p["user_id"] for p in voted}
    info["members_total"] = len(members)
    info["voted"] = [{"full_name": m["full_name"], "is_me": m["id"] == user["id"]}
                     for m in members if m["id"] in voted_ids]
    info["not_voted"] = [{"full_name": m["full_name"], "is_me": m["id"] == user["id"]}
                         for m in members if m["id"] not in voted_ids]

    report_day = db.latest_report_day(conn, lst["id"])
    info["report_day"] = report_day
    info["queue"] = []
    info["candidates"] = []
    info["swaps"] = []
    info["my_result"] = None

    if report_day:
        rows = db.get_results(conn, lst["id"], report_day)
        info["queue"] = [{
            "place": r["place"], "full_name": r["full_name"],
            "status": r["status"], "status_text": STATUS_TEXT.get(r["status"], r["status"]),
            "voted": r["status"] != "indifferent",
            "rank": r["rank"], "swapped": bool(r["swapped"]),
            "is_me": r["user_id"] == user["id"],
        } for r in rows]
        mine = db.get_result(conn, lst["id"], report_day, user["id"])
        if mine:
            info["my_result"] = {
                "place": mine["place"], "status": mine["status"],
                "status_text": STATUS_TEXT.get(mine["status"], mine["status"]),
                "rank": mine["rank"], "wishes": mine["wishes"],
                "swapped": bool(mine["swapped"]),
            }
            info["candidates"] = [{"user_id": r["user_id"], "full_name": r["full_name"],
                                   "place": r["place"]}
                                  for r in rows if r["user_id"] != user["id"]]
            info["swaps"] = [swap_payload(s, user["id"]) for s in
                             db.swaps_for_user(conn, lst["id"], report_day, user["id"])]
    return info


@app.route("/api/user/state")
@api_login_required
def api_user_state(conn, user):
    now = db.local_now()
    day = now.strftime("%Y-%m-%d")
    lists = [list_state_for_user(conn, lst, user, day, now)
             for lst in db.user_lists(conn, user["id"])]

    # Сначала то, что принимается сегодня (по времени открытия), потом остальное.
    lists.sort(key=lambda x: (not x["scheduled_today"], x["open_from"], x["name"]))

    return jsonify({
        "day": day,
        "server_time": now.strftime("%d.%m.%Y %H:%M:%S"),
        "reset_lead": db.RESET_LEAD_MINUTES,
        "password_is_default": db.check_password(
            db.DEFAULT_PASSWORD, user["pwd_hash"], user["pwd_salt"]),
        "lists": lists,
    })


def _member_list_or_error(conn, user, list_id):
    """Возвращает (лист, None) либо (None, ответ с ошибкой)."""
    lst = db.get_list(conn, list_id) if list_id else None
    if not lst:
        return None, fail("Лист не найден.", 404)
    if not db.is_member(conn, lst["id"], user["id"]):
        return None, fail("Вы не входите в состав листа «%s»." % lst["name"], 403)
    return lst, None


@app.route("/api/user/prefs", methods=["POST"])
@api_login_required
def api_save_prefs(conn, user):
    data = request.get_json(silent=True) or {}
    try:
        list_id = int(data.get("list_id"))
    except (TypeError, ValueError):
        return fail("Не указан лист голосования.")

    lst, error = _member_list_or_error(conn, user, list_id)
    if error:
        return error

    state, hint = db.list_window(conn, lst)
    if state != "open":
        db.log(conn, user["full_name"], "PREFS_DENIED",
               "«%s»: %s" % (lst["name"], hint), level="WARNING")
        return fail("«%s»: пожелания принимаются %s. %s"
                    % (lst["name"], db.schedule_text(lst), hint), 403)

    places = db.list_places(conn, lst)
    try:
        picks = [int(data.get("p1")), int(data.get("p2")), int(data.get("p3"))]
    except (TypeError, ValueError):
        return fail("Нужно выбрать три места.")
    if any(p < 1 or p > places for p in picks):
        return fail("Места должны быть в диапазоне от 1 до %d." % places)
    if len(set(picks)) != 3:
        return fail("Три места должны быть разными.")

    day = today_str()
    old = db.get_preference(conn, lst["id"], day, user["id"])
    db.save_preference(conn, lst["id"], day, user["id"], *picks)
    wish_text = ", ".join(str(p) for p in picks)
    if old:
        db.log(conn, user["full_name"], "PREFS_EDIT",
               "«%s»: было %d, %d, %d -> стало %s"
               % (lst["name"], old["p1"], old["p2"], old["p3"], wish_text))
        message = "«%s»: пожелания обновлены (%s)." % (lst["name"], wish_text)
    else:
        db.log(conn, user["full_name"], "PREFS_SAVE",
               "«%s»: выбраны места %s" % (lst["name"], wish_text))
        message = "«%s»: пожелания зафиксированы (%s)." % (lst["name"], wish_text)
    return jsonify({"ok": True, "message": message})


@app.route("/api/user/prefs", methods=["DELETE"])
@api_login_required
def api_delete_prefs(conn, user):
    data = request.get_json(silent=True) or {}
    raw = data.get("list_id") or request.args.get("list_id")
    try:
        list_id = int(raw)
    except (TypeError, ValueError):
        return fail("Не указан лист голосования.")

    lst, error = _member_list_or_error(conn, user, list_id)
    if error:
        return error

    state, hint = db.list_window(conn, lst)
    if state != "open":
        return fail("«%s»: изменение пожеланий возможно %s. %s"
                    % (lst["name"], db.schedule_text(lst), hint), 403)

    db.delete_preference(conn, lst["id"], today_str(), user["id"])
    db.log(conn, user["full_name"], "PREFS_DELETE", "«%s»: пожелания отозваны" % lst["name"])
    return jsonify({"ok": True,
                    "message": "«%s»: пожелания удалены, место будет выдано случайно."
                               % lst["name"]})


# --------------------------------------------------------- смена своего пароля

@app.route("/api/user/password", methods=["POST"])
@api_login_required
def api_change_own_password(conn, user):
    """Смена пароля самим участником: старый пароль и новый дважды."""
    data = request.get_json(silent=True) or {}
    old = data.get("old_password") or ""
    new1 = (data.get("new_password") or "").strip()
    new2 = (data.get("new_password2") or "").strip()

    if not db.check_password(old, user["pwd_hash"], user["pwd_salt"]):
        db.log(conn, user["full_name"], "PASSWORD_SELF_FAIL",
               "попытка смены пароля с неверным старым паролем", level="WARNING")
        return fail("Старый пароль указан неверно.", 403)
    if new1 != new2:
        return fail("Новый пароль введён по-разному — повторите ввод.")
    if not db.PASSWORD_RE.match(new1):
        return fail("Новый пароль — ровно %d символов: английские буквы и цифры."
                    % db.PASSWORD_LEN)
    if new1 == old:
        return fail("Новый пароль совпадает со старым.")

    db.set_user_password(conn, user["id"], new1)
    db.log(conn, user["full_name"], "PASSWORD_SELF_CHANGE", "пользователь сменил себе пароль")
    return jsonify({"ok": True,
                    "message": "Пароль изменён. В следующий раз входите с новым паролем."})


# ------------------------------------------------------------- обмен местами

@app.route("/api/user/swaps", methods=["POST"])
@api_login_required
def api_create_swap(conn, user):
    data = request.get_json(silent=True) or {}
    try:
        list_id = int(data.get("list_id"))
    except (TypeError, ValueError):
        return fail("Не указан лист голосования.")

    lst, error = _member_list_or_error(conn, user, list_id)
    if error:
        return error

    day = db.latest_report_day(conn, lst["id"])
    if not day:
        return fail("«%s»: распределение ещё не сформировано — меняться пока нечем."
                    % lst["name"])

    try:
        target_id = int(data.get("to_user_id"))
    except (TypeError, ValueError):
        return fail("Не выбран человек для обмена.")
    if target_id == user["id"]:
        return fail("Нельзя предложить обмен самому себе.")

    mine = db.get_result(conn, lst["id"], day, user["id"])
    theirs = db.get_result(conn, lst["id"], day, target_id)
    if not mine or not theirs:
        return fail("Оба участника должны иметь место в распределении за %s." % day)
    if db.pending_swap_between(conn, lst["id"], day, user["id"], target_id):
        return fail("Заявка между вами уже отправлена и ждёт ответа.")

    target = db.get_user(conn, target_id)
    swap_id = db.create_swap(conn, lst["id"], day, user["id"], target_id,
                             mine["place"], theirs["place"])
    db.log(conn, user["full_name"], "SWAP_REQUEST",
           "«%s»: предложен обмен %s (место %d) <-> %s (место %d), заявка №%d"
           % (lst["name"], user["full_name"], mine["place"], target["full_name"],
              theirs["place"], swap_id), day=day)
    return jsonify({"ok": True, "message": "Предложение обмена отправлено: %s (место %d)."
                                           % (target["full_name"], theirs["place"])})


@app.route("/api/user/swaps/<int:swap_id>", methods=["POST"])
@api_login_required
def api_respond_swap(conn, user, swap_id):
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    swap = db.get_swap(conn, swap_id)
    if not swap:
        return fail("Заявка не найдена.", 404)
    if swap["status"] != "pending":
        return fail("Заявка уже обработана (%s)."
                    % SWAP_STATUS_TEXT.get(swap["status"], swap["status"]))

    lst = db.get_list(conn, swap["list_id"])
    day = swap["day"]
    author = db.get_user(conn, swap["from_user"])
    target = db.get_user(conn, swap["to_user"])
    name = lst["name"] if lst else "лист"

    if action == "cancel":
        if swap["from_user"] != user["id"]:
            return fail("Отозвать заявку может только её автор.", 403)
        db.set_swap_status(conn, swap_id, "cancelled")
        db.log(conn, user["full_name"], "SWAP_CANCEL",
               "«%s»: заявка №%d отозвана" % (name, swap_id), day=day)
        return jsonify({"ok": True, "message": "Заявка отозвана."})

    if swap["to_user"] != user["id"]:
        return fail("Отвечать на заявку может только её получатель.", 403)

    if action == "decline":
        db.set_swap_status(conn, swap_id, "declined")
        db.log(conn, user["full_name"], "SWAP_DECLINE",
               "«%s»: отказ на заявку №%d от %s" % (name, swap_id, author["full_name"]),
               day=day)
        return jsonify({"ok": True, "message": "Вы отказали в обмене."})

    if action != "accept":
        return fail("Неизвестное действие.")

    mine = db.get_result(conn, swap["list_id"], day, user["id"])
    theirs = db.get_result(conn, swap["list_id"], day, swap["from_user"])
    if not mine or not theirs:
        db.set_swap_status(conn, swap_id, "expired")
        return fail("Распределение изменилось, заявка больше не актуальна.")
    if mine["place"] != swap["to_place"] or theirs["place"] != swap["from_place"]:
        db.set_swap_status(conn, swap_id, "expired")
        db.log(conn, user["full_name"], "SWAP_EXPIRED",
               "«%s»: заявка №%d устарела, места уже изменились" % (name, swap_id),
               level="WARNING", day=day)
        return fail("Места уже изменились — заявка неактуальна.")

    db.apply_swap(conn, swap["list_id"], day, swap["from_user"], theirs["place"],
                  user["id"], mine["place"])
    db.set_swap_status(conn, swap_id, "accepted")
    db.log(conn, user["full_name"], "SWAP_ACCEPT",
           "«%s»: обмен по заявке №%d — %s теперь на месте %d, %s на месте %d"
           % (name, swap_id, author["full_name"], mine["place"], target["full_name"],
              theirs["place"]), day=day)
    return jsonify({"ok": True, "message": "Обмен выполнен: ваше новое место %d."
                                           % theirs["place"]})


# --------------------------------------------------------- листы (администратор)

def admin_list_payload(conn, lst, now, day):
    info = list_brief(conn, lst, now)
    info["member_ids"] = db.list_member_ids(conn, lst["id"])
    info["members_total"] = len(info["member_ids"])
    info["submitted"] = len(db.all_preferences(conn, lst["id"], day))
    info["report_days"] = db.report_days(conn, lst["id"])
    meta = db.get_report_meta(conn, lst["id"], day)
    info["meta"] = ({"created_at": meta["created_at"], "created_by": meta["created_by"],
                     "summary": meta["summary"]} if meta else None)
    info["last_reset"] = db.get_setting(conn, "last_reset_%d" % lst["id"])
    return info


@app.route("/api/admin/lists")
@api_admin_required
def api_admin_lists(conn, user):
    now = db.local_now()
    day = request.args.get("day") or today_str()
    return jsonify({
        "day": day,
        "test_mode": db.test_mode(conn),
        "reset_lead": db.RESET_LEAD_MINUTES,
        "weekday_names": db.WEEKDAY_SHORT,
        "users": [{"id": u["id"], "full_name": u["full_name"],
                   "is_admin": bool(u["is_admin"])} for u in db.all_users(conn)],
        "lists": [admin_list_payload(conn, lst, now, day) for lst in db.all_lists(conn)],
    })


def _list_form(data):
    """Разбирает поля листа из запроса."""
    name = (data.get("name") or "").strip()
    if not name:
        return None, "У листа должно быть название."
    weekdays = db.clean_weekdays(data.get("weekdays"))
    if not weekdays:
        return None, "Выберите хотя бы один день недели."
    open_from = db.parse_hhmm(data.get("open_from"), db.DEFAULT_OPEN_FROM)
    open_to = db.parse_hhmm(data.get("open_to"), db.DEFAULT_OPEN_TO)
    if open_from >= open_to:
        return None, "Время открытия должно быть раньше времени закрытия."
    try:
        places = int(data.get("places") or 0)
    except (TypeError, ValueError):
        return None, "Количество мест должно быть числом."
    if places < 0:
        return None, "Количество мест не может быть отрицательным."
    members = [int(x) for x in (data.get("member_ids") or [])]
    if places and members and places < len(members):
        return None, ("Мест (%d) меньше, чем участников листа (%d)."
                      % (places, len(members)))
    return {
        "name": name,
        "description": (data.get("description") or "").strip(),
        "weekdays": weekdays,
        "open_from": open_from.strftime("%H:%M"),
        "open_to": open_to.strftime("%H:%M"),
        "places": places,
        "active": 1 if data.get("active", True) else 0,
        "member_ids": members,
    }, None


@app.route("/api/admin/lists", methods=["POST"])
@api_admin_required
def api_admin_create_list(conn, user):
    form, error = _list_form(request.get_json(silent=True) or {})
    if error:
        return fail(error)
    list_id = db.create_list(conn, form["name"], form["description"], form["weekdays"],
                             form["open_from"], form["open_to"], form["places"],
                             form["active"], form["member_ids"])
    db.log(conn, user["full_name"], "LIST_CREATE",
           "создан лист «%s»: %s, участников %d"
           % (form["name"], db.schedule_text(db.get_list(conn, list_id)),
              len(form["member_ids"])))
    return jsonify({"ok": True, "list_id": list_id,
                    "message": "Лист «%s» создан." % form["name"]})


@app.route("/api/admin/lists/<int:list_id>", methods=["POST"])
@api_admin_required
def api_admin_update_list(conn, user, list_id):
    lst = db.get_list(conn, list_id)
    if not lst:
        return fail("Лист не найден.", 404)
    form, error = _list_form(request.get_json(silent=True) or {})
    if error:
        return fail(error)
    db.update_list(conn, list_id, form["name"], form["description"], form["weekdays"],
                   form["open_from"], form["open_to"], form["places"], form["active"])
    db.set_list_members(conn, list_id, form["member_ids"])
    db.log(conn, user["full_name"], "LIST_UPDATE",
           "изменён лист «%s»: %s, участников %d, %s"
           % (form["name"], db.schedule_text(db.get_list(conn, list_id)),
              len(form["member_ids"]), "включён" if form["active"] else "выключен"))
    return jsonify({"ok": True, "message": "Лист «%s» сохранён." % form["name"]})


@app.route("/api/admin/lists/<int:list_id>/delete", methods=["POST"])
@api_admin_required
def api_admin_delete_list(conn, user, list_id):
    lst = db.get_list(conn, list_id)
    if not lst:
        return fail("Лист не найден.", 404)
    db.delete_list(conn, list_id)
    db.log(conn, user["full_name"], "LIST_DELETE",
           "удалён лист «%s» вместе с его пожеланиями и распределениями" % lst["name"],
           level="WARNING")
    return jsonify({"ok": True, "message": "Лист «%s» удалён." % lst["name"]})


# ------------------------------------------------------ раздел администратора

@app.route("/api/admin/overview")
@api_admin_required
def api_admin_overview(conn, user):
    day = request.args.get("day") or today_str()
    now = db.local_now()
    lists = db.all_lists(conn)
    try:
        list_id = int(request.args.get("list_id") or 0)
    except (TypeError, ValueError):
        list_id = 0
    current = db.get_list(conn, list_id) if list_id else (lists[0] if lists else None)
    if not current:
        return jsonify({"day": day, "lists": [], "list": None, "people": [],
                        "test_mode": db.test_mode(conn), "events": []})

    prefs = {p["user_id"]: p for p in db.all_preferences(conn, current["id"], day)}
    people = []
    for u in db.list_member_rows(conn, current["id"]):
        p = prefs.get(u["id"])
        people.append({
            "id": u["id"], "full_name": u["full_name"], "is_admin": bool(u["is_admin"]),
            "wishes": ("%d, %d, %d" % (p["p1"], p["p2"], p["p3"])) if p else None,
            "updated_at": p["updated_at"] if p else None,
        })

    return jsonify({
        "day": day,
        "test_mode": db.test_mode(conn),
        "lists": [{"id": x["id"], "name": x["name"]} for x in lists],
        "list": admin_list_payload(conn, current, now, day),
        "people": people,
        "submitted": len(prefs),
        "events": [dict(e) for e in db.read_events(conn, day, day)[-60:]],
    })


@app.route("/api/admin/password", methods=["POST"])
@api_admin_required
def api_admin_password(conn, user):
    data = request.get_json(silent=True) or {}
    try:
        target_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return fail("Не выбран пользователь.")
    target = db.get_user(conn, target_id)
    if not target:
        return fail("Пользователь не найден.", 404)

    if data.get("mode") == "manual":
        password = (data.get("password") or "").strip()
        if not db.PASSWORD_RE.match(password):
            return fail("Пароль должен состоять ровно из %d символов: "
                        "английские буквы и цифры." % db.PASSWORD_LEN)
    else:
        password = db.generate_password()

    db.set_user_password(conn, target_id, password)
    db.log(conn, user["full_name"], "PASSWORD_CHANGE",
           "новый пароль выдан пользователю: %s" % target["full_name"])
    return jsonify({"ok": True, "full_name": target["full_name"], "password": password,
                    "message": "Пароль пользователя %s изменён." % target["full_name"]})


@app.route("/api/admin/settings", methods=["POST"])
@api_admin_required
def api_admin_settings(conn, user):
    data = request.get_json(silent=True) or {}
    if "test_mode" in data:
        new_mode = "1" if data.get("test_mode") else "0"
        if new_mode != db.get_setting(conn, "test_mode", "0"):
            db.set_setting(conn, "test_mode", new_mode)
            db.log(conn, user["full_name"], "SETTINGS",
                   "режим отладки (приём открыт круглосуточно): %s"
                   % ("включён" if new_mode == "1" else "выключен"), level="WARNING")
    return jsonify({"ok": True, "message": "Настройки сохранены."})


@app.route("/api/admin/reset", methods=["POST"])
@api_admin_required
def api_admin_reset(conn, user):
    data = request.get_json(silent=True) or {}
    raw = data.get("list_id")
    if raw:
        lst = db.get_list(conn, int(raw))
        if not lst:
            return fail("Лист не найден.", 404)
        prefs, swaps = db.reset_list(conn, lst, user["full_name"],
                                     "ручная подготовка к новому расчёту",
                                     include_today=True)
        where = "«%s»" % lst["name"]
    else:
        prefs, swaps = db.reset_all_lists(conn, user["full_name"])
        where = "все листы"
    return jsonify({"ok": True,
                    "message": "%s: стёрто пожеланий %d, отменено заявок на обмен %d."
                               % (where, prefs, swaps)})


@app.route("/api/admin/compute", methods=["POST"])
@api_admin_required
def api_admin_compute(conn, user):
    data = request.get_json(silent=True) or {}
    try:
        list_id = int(data.get("list_id"))
    except (TypeError, ValueError):
        return fail("Не указан лист голосования.")
    lst = db.get_list(conn, list_id)
    if not lst:
        return fail("Лист не найден.", 404)

    day = data.get("day") or today_str()
    places = db.list_places(conn, lst)
    members = db.list_member_rows(conn, lst["id"])
    if not members:
        return fail("В листе «%s» нет участников." % lst["name"])
    participants = [(u["id"], u["full_name"]) for u in members]
    pref_rows = db.all_preferences(conn, lst["id"], day)
    prefs = {p["user_id"]: (p["p1"], p["p2"], p["p3"]) for p in pref_rows}
    # Очередь голосования: кто раньше отдал пожелания, тот в приоритете, если
    # несколько раскладов одинаково хороши.
    by_time = sorted(pref_rows, key=lambda p: (p["updated_at"], p["user_id"]))
    order = {p["user_id"]: idx for idx, p in enumerate(by_time)}

    state, _ = db.list_window(conn, lst)
    if day == today_str() and state == "open":
        db.log(conn, user["full_name"], "CALC_EARLY",
               "«%s»: расчёт запущен до закрытия приёма пожеланий" % lst["name"],
               level="WARNING", day=day)

    db.log(conn, user["full_name"], "CALC_START",
           "«%s» за %s: участников %d, пожеланий %d, мест %d"
           % (lst["name"], day, len(participants), len(prefs), places), day=day)
    if by_time:
        db.log(conn, "РАСЧЁТ «%s»" % lst["name"], "CALC_ORDER",
               "очередь голосования: %s"
               % "; ".join("%d) %s в %s" % (i + 1, p["full_name"], p["updated_at"])
                           for i, p in enumerate(by_time)), day=day)

    try:
        rows, log_lines, stats = assignment.solve(participants, prefs, places, order=order)
    except ValueError as exc:
        db.log(conn, user["full_name"], "CALC_ERROR", "«%s»: %s" % (lst["name"], exc),
               level="ERROR", day=day)
        return fail("Расчёт невозможен: %s" % exc)

    for line in log_lines:
        if line:
            db.log(conn, "РАСЧЁТ «%s»" % lst["name"], "CALC", line, day=day)

    summary = ("Учтено пожеланий: %d из %d (1-е: %d, 2-е: %d, 3-е: %d); "
               "не учтено: %d; без пожеланий: %d"
               % (stats["satisfied"], stats["voters"], stats["rank1"], stats["rank2"],
                  stats["rank3"], stats["missed"], stats["silent"]))

    db.save_results(conn, lst["id"], day,
                    [(lst["id"], day, r["user_id"], r["place"], r["status"], r["rank"],
                      r["wishes"]) for r in rows], user["full_name"], summary)
    db.log(conn, user["full_name"], "CALC_DONE", "«%s»: %s" % (lst["name"], summary),
           day=day)
    return jsonify({"ok": True, "day": day, "list_id": lst["id"], "summary": summary,
                    "message": "«%s»: распределение за %s сформировано."
                               % (lst["name"], day)})


@app.route("/api/admin/report")
@api_admin_required
def api_admin_report(conn, user):
    try:
        list_id = int(request.args.get("list_id") or 0)
    except (TypeError, ValueError):
        return fail("Не указан лист голосования.")
    lst = db.get_list(conn, list_id)
    if not lst:
        return fail("Лист не найден.", 404)
    day = request.args.get("day") or today_str()
    results = db.get_results(conn, lst["id"], day)
    if not results:
        return fail("«%s»: распределение за %s не формировалось." % (lst["name"], day), 404)
    meta = db.get_report_meta(conn, lst["id"], day)
    return jsonify({
        "day": day,
        "list": {"id": lst["id"], "name": lst["name"],
                 "description": lst["description"],
                 "schedule_text": db.schedule_text(lst)},
        "meta": {"created_at": meta["created_at"], "created_by": meta["created_by"],
                 "summary": meta["summary"]} if meta else None,
        "rows": [{"place": r["place"], "surname": r["surname"], "name": r["name"],
                  "patronymic": r["patronymic"], "full_name": r["full_name"],
                  "wishes": r["wishes"], "rank": r["rank"], "status": r["status"],
                  "status_text": STATUS_TEXT.get(r["status"], r["status"]),
                  "swapped": bool(r["swapped"])} for r in results],
        "swaps": [{"id": s["id"], "from_name": s["from_name"], "to_name": s["to_name"],
                   "from_place": s["from_place"], "to_place": s["to_place"],
                   "status": s["status"],
                   "status_text": SWAP_STATUS_TEXT.get(s["status"], s["status"]),
                   "created_at": s["created_at"], "resolved_at": s["resolved_at"]}
                  for s in db.swaps_for_day(conn, lst["id"], day)],
        "events": [dict(e) for e in db.read_events(conn, day, day)],
    })


@app.route("/api/admin/logs")
@api_admin_required
def api_admin_logs(conn, user):
    day_to = request.args.get("to") or today_str()
    try:
        default_from = (datetime.strptime(day_to, "%Y-%m-%d") -
                        timedelta(days=7)).strftime("%Y-%m-%d")
    except ValueError:
        return fail("Некорректная дата.")
    day_from = request.args.get("from") or default_from
    events = db.read_events(conn, day_from, day_to)
    return jsonify({"from": day_from, "to": day_to,
                    "events": [dict(e) for e in events]})


# ------------------------------------------------------------------ выгрузки

def _report_csv(conn, lst, day):
    results = db.get_results(conn, lst["id"], day)
    meta = db.get_report_meta(conn, lst["id"], day)
    created = meta["created_at"] if meta else db.local_now().strftime("%Y-%m-%d %H:%M:%S")
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Отчёт по очереди сдачи лабораторной работы"])
    writer.writerow(["Лист", lst["name"]])
    writer.writerow(["Описание", lst["description"]])
    writer.writerow(["Расписание приёма", db.schedule_text(lst)])
    writer.writerow(["Дата сдачи", day])
    writer.writerow(["Дата формирования", created])
    if meta:
        writer.writerow(["Сформировал", meta["created_by"]])
        writer.writerow(["Итог", meta["summary"]])
    writer.writerow([])
    writer.writerow(["Место", "Фамилия", "Имя", "Отчество", "Пожелания",
                     "Результат", "Номер сработавшего пожелания", "Был обмен"])
    for row in results:
        writer.writerow([row["place"], row["surname"], row["name"], row["patronymic"],
                         row["wishes"] or "-", STATUS_TEXT.get(row["status"], row["status"]),
                         row["rank"] or "-", "да" if row["swapped"] else "нет"])
    return buf.getvalue()


@app.route("/api/admin/import", methods=["POST"])
@api_admin_required
def api_admin_import(conn, user):
    """Загружает ранее выгруженный CSV обратно как готовое распределение."""
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return fail("Файл не выбран.")
    try:
        parsed = report_import.parse_report_csv(
            report_import.decode_csv(upload.read()))
    except ValueError as exc:
        db.log(conn, user["full_name"], "IMPORT_ERROR",
               "файл %s не разобран: %s" % (upload.filename, exc), level="WARNING")
        return fail("Не удалось прочитать файл: %s" % exc)

    # 1. В какой лист загружаем: выбранный вручную или найденный по названию.
    raw_list = (request.form.get("list_id") or "").strip()
    if raw_list.isdigit():
        lst = db.get_list(conn, int(raw_list))
    else:
        lst = None
        for candidate in db.all_lists(conn):
            if candidate["name"].strip().lower() == parsed["list_name"].lower():
                lst = candidate
                break
        if not lst:
            return fail("В файле указан лист «%s», но такого листа на сайте нет — "
                        "выберите лист вручную." % parsed["list_name"])
    if not lst:
        return fail("Лист не найден.", 404)

    day = (request.form.get("day") or parsed["day"]).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return fail("В файле не указана дата сдачи — задайте её вручную.")

    # 2. Сопоставляем людей из файла с участниками сайта.
    members = {u["id"]: u for u in db.list_member_rows(conn, lst["id"])}
    by_key = {db.login_key("%s %s" % (u["surname"], u["name"])): u
              for u in db.all_users(conn)}
    resolved, unknown, outsiders = [], [], []
    for row in parsed["rows"]:
        person = by_key.get(db.login_key("%s %s" % (row["surname"], row["name"])))
        if not person:
            unknown.append("строка %d: %s %s" % (row["line"], row["surname"], row["name"]))
            continue
        if person["id"] not in members:
            outsiders.append(person["full_name"])
        resolved.append((person, row))
    if unknown:
        return fail("В файле есть люди, которых нет в списке: %s."
                    % "; ".join(unknown[:5]))

    # 3. Проверяем, что очередь корректная.
    places = [row["place"] for _, row in resolved]
    duplicates = sorted({p for p in places if places.count(p) > 1})
    if duplicates:
        return fail("Одно и то же место занято несколько раз: %s."
                    % ", ".join(str(p) for p in duplicates))
    ids = [person["id"] for person, _ in resolved]
    if len(set(ids)) != len(ids):
        return fail("Один и тот же человек встречается в файле дважды.")
    limit = db.list_places(conn, lst)
    beyond = [p for p in places if p < 1 or p > limit]
    if beyond:
        return fail("В листе «%s» всего %d мест, а в файле есть место %d."
                    % (lst["name"], limit, beyond[0]))

    # 4. Записываем распределение.
    existed = bool(db.get_results(conn, lst["id"], day))
    summary = parsed["summary"] or ("загружено из файла %s" % upload.filename)
    db.save_results(conn, lst["id"], day,
                    [(lst["id"], day, person["id"], row["place"], row["status"],
                      row["rank"], row["wishes"]) for person, row in resolved],
                    "загрузка файла (%s)" % user["full_name"], summary)
    for person, row in resolved:
        if row["swapped"]:
            conn.execute("UPDATE results SET swapped = 1 WHERE list_id = ? AND day = ?"
                         " AND user_id = ?", (lst["id"], day, person["id"]))
    conn.execute("UPDATE swaps SET status = 'expired', resolved_at = ? WHERE list_id = ?"
                 " AND day = ? AND status = 'pending'",
                 (db.local_now().strftime("%Y-%m-%d %H:%M:%S"), lst["id"], day))
    conn.commit()

    warnings = []
    missing = [u["full_name"] for uid, u in members.items()
               if uid not in {person["id"] for person, _ in resolved}]
    if missing:
        warnings.append("состоят в листе, но их нет в файле: %s" % ", ".join(missing))
    if outsiders:
        warnings.append("есть в файле, но не состоят в листе: %s" % ", ".join(outsiders))
    if existed:
        warnings.append("прежнее распределение за %s заменено" % day)

    db.log(conn, user["full_name"], "IMPORT",
           "«%s» за %s: загружено распределение из файла %s, строк %d%s"
           % (lst["name"], day, upload.filename, len(resolved),
              ("; " + "; ".join(warnings)) if warnings else ""),
           level="WARNING", day=day)
    return jsonify({"ok": True, "list_id": lst["id"], "day": day,
                    "imported": len(resolved), "warnings": warnings,
                    "message": "«%s»: загружено распределение за %s (%d человек)."
                               % (lst["name"], day, len(resolved))})


def _report_log(conn, day):
    events = db.read_events(conn, day, day)
    lines = ["Журнал событий за %s" % day,
             "Выгружен: %s" % db.local_now().strftime("%Y-%m-%d %H:%M:%S"), ""]
    for e in events:
        lines.append("%s | %-7s | %-30s | %-24s | %s"
                     % (e["ts"], e["level"], e["actor"], e["action"], e["message"]))
    return "\n".join(lines) + "\n"


def _download_guard():
    conn = get_conn()
    user = current_user(conn)
    if not user or not user["is_admin"]:
        conn.close()
        abort(403)
    return conn, user


def _safe_name(text):
    """Название листа, пригодное для имени файла."""
    return re.sub(r"[^\w\-. ]+", "_", text, flags=re.UNICODE).strip() or "лист"


@app.route("/download/report/<int:list_id>/<day>.csv")
def download_csv(list_id, day):
    conn, user = _download_guard()
    try:
        lst = db.get_list(conn, list_id)
        if not lst or not db.get_results(conn, list_id, day):
            abort(404)
        data = ("﻿" + _report_csv(conn, lst, day)).encode("utf-8")
        db.log(conn, user["full_name"], "EXPORT_CSV",
               "«%s»: выгрузка отчёта за %s" % (lst["name"], day), day=day)
        return send_file(io.BytesIO(data), mimetype="text/csv; charset=utf-8",
                         as_attachment=True,
                         download_name="Очередь_%s_%s.csv" % (_safe_name(lst["name"]), day))
    finally:
        conn.close()


@app.route("/download/log/<day>.log")
def download_log(day):
    conn, user = _download_guard()
    try:
        data = _report_log(conn, day).encode("utf-8")
        db.log(conn, user["full_name"], "EXPORT_LOG", "выгрузка журнала за %s" % day, day=day)
        return send_file(io.BytesIO(data), mimetype="text/plain; charset=utf-8",
                         as_attachment=True, download_name="Журнал_%s.log" % day)
    finally:
        conn.close()


@app.route("/download/report/<int:list_id>/<day>.zip")
def download_zip(list_id, day):
    conn, user = _download_guard()
    try:
        lst = db.get_list(conn, list_id)
        if not lst or not db.get_results(conn, list_id, day):
            abort(404)
        safe = _safe_name(lst["name"])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Очередь_%s_%s.csv" % (safe, day),
                        ("﻿" + _report_csv(conn, lst, day)).encode("utf-8"))
            zf.writestr("Журнал_%s.log" % day, _report_log(conn, day).encode("utf-8"))
        buf.seek(0)
        db.log(conn, user["full_name"], "EXPORT_ZIP",
               "«%s»: выгрузка отчёта и журнала за %s" % (lst["name"], day), day=day)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name="Отчет_%s_%s.zip" % (safe, day))
    finally:
        conn.close()


# ------------------------------------------- фоновая подготовка листов к приёму

def _reset_worker():
    while True:
        wait = 300
        try:
            conn = get_conn()
            try:
                wait = min(max(db.seconds_to_next_reset(conn), 10), 300)
            finally:
                conn.close()
        except Exception as exc:                  # noqa: BLE001
            print("Ошибка планировщика: %s" % exc)
        time_mod.sleep(wait)
        try:
            conn = get_conn()
            try:
                db.ensure_list_resets(conn)
            finally:
                conn.close()
        except Exception as exc:                  # noqa: BLE001
            print("Ошибка подготовки листов: %s" % exc)


def start_scheduler():
    thread = threading.Thread(target=_reset_worker, daemon=True, name="list-reset")
    thread.start()
    return thread


# --------------------------------------------------------------- старт сервиса

def startup(seed=True, scheduler=True):
    """Готовит приложение к работе: схема, список людей, планировщик подготовки."""
    print("Хранилище: %s" % ("Postgres (DATABASE_URL)" if db.USE_POSTGRES
                             else "SQLite %s" % db.DB_PATH))
    try:
        conn = get_conn()
        try:
            if seed and os.environ.get("AUTO_SEED", "1") == "1":
                created = db.ensure_seeded(conn)
                if created:
                    print("Заведено пользователей: %d, стартовый пароль: %s"
                          % (len(created), db.DEFAULT_PASSWORD))
            if not db.all_users(conn):
                print("База пуста. Выполните: python init_db.py")
        finally:
            conn.close()
    except Exception as exc:                      # noqa: BLE001
        # Сервис всё равно поднимаем: иначе хостинг уходит в бесконечный
        # перезапуск, а причина не видна. Ошибку покажет /healthz.
        print("ОШИБКА подключения к базе: %s" % exc)
    if scheduler:
        start_scheduler()


startup(scheduler=os.environ.get("RUN_SCHEDULER", "1") == "1")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
