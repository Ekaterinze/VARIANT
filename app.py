# -*- coding: utf-8 -*-
"""Сайт выбора места (очереди) для сдачи лабораторной работы.

Backend: Flask + SQLite, отдаёт JSON API и один HTML-каркас.
Frontend: Vue 3 (static/app.js), подключается без сборки.

Правила работы:
  * обычный пользователь входит только с 20:00 до 21:00;
  * администратор (Пархачева Екатерина) входит в любое время;
  * в 19:55 все пожелания стираются, сайт готов к новому расчёту;
  * после расчёта участники могут предлагать друг другу обмен местами.
"""

import io
import os
import csv
import time as time_mod
import zipfile
import secrets
import threading
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (Flask, render_template, request, session, jsonify,
                   send_file, abort)

import db
import assignment

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
    "indifferent": "пожеланий не было, место назначено случайно",
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


_reset_checked_for = None


@app.before_request
def before_any_request():
    """Гарантирует, что сброс в 19:55 произойдёт, даже если сервер был выключен.

    Проверка делается не чаще раза в день на процесс: до 19:55 делать нечего,
    а после первого срабатывания повторно ходить в базу незачем.
    """
    global _reset_checked_for
    now = db.local_now()
    if now.time() < db.RESET_AT:
        return
    today = now.strftime("%Y-%m-%d")
    if _reset_checked_for == today:
        return
    conn = get_conn()
    try:
        db.ensure_daily_reset(conn, now)
        _reset_checked_for = today
    finally:
        conn.close()


def window_payload(conn):
    state, hint = db.window_state(conn)
    return {
        "state": state,
        "hint": hint,
        "open_from": db.OPEN_FROM.strftime("%H:%M"),
        "open_to": db.OPEN_TO.strftime("%H:%M"),
        "reset_at": db.RESET_AT.strftime("%H:%M"),
        "seconds_to_open": db.seconds_to_open(),
        "seconds_to_reset": db.seconds_to_reset(),
        "test_mode": db.test_mode(conn),
    }


def user_payload(user):
    return {"id": user["id"], "full_name": user["full_name"],
            "surname": user["surname"], "name": user["name"],
            "is_admin": bool(user["is_admin"])}


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
            info["window"] = db.window_state(conn)[0]
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
            "window": window_payload(conn),
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

        state, hint = db.window_state(conn)
        if state != "open" and not user["is_admin"]:
            db.log(conn, user["full_name"], "LOGIN_DENIED", hint, level="WARNING")
            return fail("Сайт открыт с %s до %s. %s" % (
                db.OPEN_FROM.strftime("%H:%M"), db.OPEN_TO.strftime("%H:%M"), hint), 403)

        session["user_id"] = user["id"]
        db.log(conn, user["full_name"], "LOGIN",
               "вход выполнен%s" % (" (администратор)" if user["is_admin"] else ""))
        return jsonify({"user": user_payload(user), "window": window_payload(conn)})
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


@app.route("/api/user/state")
@api_login_required
def api_user_state(conn, user):
    day = today_str()
    pref = db.get_preference(conn, day, user["id"])
    state, _ = db.window_state(conn)
    can_edit = (state == "open")

    report_day = db.latest_report_day(conn)
    my_result = db.get_result(conn, report_day, user["id"]) if report_day else None

    candidates, swaps = [], []
    if report_day and my_result:
        for row in db.get_results(conn, report_day):
            if row["user_id"] != user["id"]:
                candidates.append({"user_id": row["user_id"],
                                   "full_name": row["full_name"],
                                   "place": row["place"]})
        swaps = [swap_payload(s, user["id"])
                 for s in db.swaps_for_user(conn, report_day, user["id"])]

    return jsonify({
        "day": day,
        "places": db.places_count(conn),
        "can_edit": can_edit,
        "password_is_default": db.check_password(
            db.DEFAULT_PASSWORD, user["pwd_hash"], user["pwd_salt"]),
        "pref": ({"p1": pref["p1"], "p2": pref["p2"], "p3": pref["p3"],
                  "updated_at": pref["updated_at"]} if pref else None),
        "report_day": report_day,
        "my_result": ({"place": my_result["place"], "status": my_result["status"],
                       "status_text": STATUS_TEXT.get(my_result["status"], my_result["status"]),
                       "rank": my_result["rank"], "wishes": my_result["wishes"],
                       "swapped": bool(my_result["swapped"])} if my_result else None),
        "candidates": candidates,
        "swaps": swaps,
        "window": window_payload(conn),
    })


@app.route("/api/user/prefs", methods=["POST"])
@api_login_required
def api_save_prefs(conn, user):
    day = today_str()
    state, hint = db.window_state(conn)
    if state != "open":
        db.log(conn, user["full_name"], "PREFS_DENIED", hint, level="WARNING")
        return fail("Пожелания принимаются только с %s до %s. %s" % (
            db.OPEN_FROM.strftime("%H:%M"), db.OPEN_TO.strftime("%H:%M"), hint), 403)

    data = request.get_json(silent=True) or {}
    places = db.places_count(conn)
    try:
        picks = [int(data.get("p1")), int(data.get("p2")), int(data.get("p3"))]
    except (TypeError, ValueError):
        return fail("Нужно выбрать три места.")
    if any(p < 1 or p > places for p in picks):
        return fail("Места должны быть в диапазоне от 1 до %d." % places)
    if len(set(picks)) != 3:
        return fail("Три места должны быть разными.")

    old = db.get_preference(conn, day, user["id"])
    db.save_preference(conn, day, user["id"], *picks)
    wish_text = ", ".join(str(p) for p in picks)
    if old:
        db.log(conn, user["full_name"], "PREFS_EDIT",
               "было: %d, %d, %d -> стало: %s" % (old["p1"], old["p2"], old["p3"], wish_text))
        message = "Пожелания обновлены: %s." % wish_text
    else:
        db.log(conn, user["full_name"], "PREFS_SAVE", "выбраны места: %s" % wish_text)
        message = "Пожелания зафиксированы: %s." % wish_text
    return jsonify({"ok": True, "message": message})


@app.route("/api/user/prefs", methods=["DELETE"])
@api_login_required
def api_delete_prefs(conn, user):
    state, _ = db.window_state(conn)
    if state != "open":
        return fail("Изменение пожеланий возможно только с %s до %s." % (
            db.OPEN_FROM.strftime("%H:%M"), db.OPEN_TO.strftime("%H:%M")), 403)
    db.delete_preference(conn, today_str(), user["id"])
    db.log(conn, user["full_name"], "PREFS_DELETE", "пожелания отозваны")
    return jsonify({"ok": True, "message": "Пожелания удалены. Место будет выдано случайно."})


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
        return fail("Новый пароль — ровно 6 символов: английские буквы и цифры.")
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
    state, _ = db.window_state(conn)
    if state != "open" and not user["is_admin"]:
        return fail("Обмен местами доступен только с %s до %s." % (
            db.OPEN_FROM.strftime("%H:%M"), db.OPEN_TO.strftime("%H:%M")), 403)

    day = db.latest_report_day(conn)
    if not day:
        return fail("Распределение ещё не сформировано — меняться пока нечем.")

    try:
        target_id = int(data.get("to_user_id"))
    except (TypeError, ValueError):
        return fail("Не выбран человек для обмена.")
    if target_id == user["id"]:
        return fail("Нельзя предложить обмен самому себе.")

    mine = db.get_result(conn, day, user["id"])
    theirs = db.get_result(conn, day, target_id)
    if not mine or not theirs:
        return fail("Оба участника должны иметь место в распределении за %s." % day)
    if db.pending_swap_between(conn, day, user["id"], target_id):
        return fail("Заявка между вами уже отправлена и ждёт ответа.")

    target = db.get_user(conn, target_id)
    swap_id = db.create_swap(conn, day, user["id"], target_id, mine["place"], theirs["place"])
    db.log(conn, user["full_name"], "SWAP_REQUEST",
           "предложен обмен: %s (место %d) <-> %s (место %d), заявка №%d"
           % (user["full_name"], mine["place"], target["full_name"], theirs["place"], swap_id),
           day=day)
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

    state, _ = db.window_state(conn)
    if state != "open" and not user["is_admin"]:
        return fail("Обмен местами доступен только с %s до %s." % (
            db.OPEN_FROM.strftime("%H:%M"), db.OPEN_TO.strftime("%H:%M")), 403)

    day = swap["day"]
    author = db.get_user(conn, swap["from_user"])
    target = db.get_user(conn, swap["to_user"])

    if action == "cancel":
        if swap["from_user"] != user["id"]:
            return fail("Отозвать заявку может только её автор.", 403)
        db.set_swap_status(conn, swap_id, "cancelled")
        db.log(conn, user["full_name"], "SWAP_CANCEL",
               "заявка №%d отозвана" % swap_id, day=day)
        return jsonify({"ok": True, "message": "Заявка отозвана."})

    if swap["to_user"] != user["id"]:
        return fail("Отвечать на заявку может только её получатель.", 403)

    if action == "decline":
        db.set_swap_status(conn, swap_id, "declined")
        db.log(conn, user["full_name"], "SWAP_DECLINE",
               "отказ на заявку №%d от %s" % (swap_id, author["full_name"]), day=day)
        return jsonify({"ok": True, "message": "Вы отказали в обмене."})

    if action != "accept":
        return fail("Неизвестное действие.")

    mine = db.get_result(conn, day, user["id"])
    theirs = db.get_result(conn, day, swap["from_user"])
    if not mine or not theirs:
        db.set_swap_status(conn, swap_id, "expired")
        return fail("Распределение изменилось, заявка больше не актуальна.")
    if mine["place"] != swap["to_place"] or theirs["place"] != swap["from_place"]:
        db.set_swap_status(conn, swap_id, "expired")
        db.log(conn, user["full_name"], "SWAP_EXPIRED",
               "заявка №%d устарела: места уже изменились" % swap_id, level="WARNING", day=day)
        return fail("Места уже изменились — заявка неактуальна.")

    db.apply_swap(conn, day, swap["from_user"], theirs["place"], user["id"], mine["place"])
    db.set_swap_status(conn, swap_id, "accepted")
    db.log(conn, user["full_name"], "SWAP_ACCEPT",
           "обмен выполнен по заявке №%d: %s теперь на месте %d, %s — на месте %d"
           % (swap_id, author["full_name"], mine["place"], target["full_name"],
              theirs["place"]), day=day)
    return jsonify({"ok": True, "message": "Обмен выполнен: ваше новое место %d."
                                           % theirs["place"]})


# ------------------------------------------------------ раздел администратора

@app.route("/api/admin/overview")
@api_admin_required
def api_admin_overview(conn, user):
    day = request.args.get("day") or today_str()
    prefs = {p["user_id"]: p for p in db.all_preferences(conn, day)}
    people = []
    for u in db.all_users(conn):
        p = prefs.get(u["id"])
        people.append({
            "id": u["id"], "full_name": u["full_name"], "is_admin": bool(u["is_admin"]),
            "wishes": ("%d, %d, %d" % (p["p1"], p["p2"], p["p3"])) if p else None,
            "updated_at": p["updated_at"] if p else None,
        })
    meta = db.get_report_meta(conn, day)
    return jsonify({
        "day": day,
        "places": db.places_count(conn),
        "test_mode": db.test_mode(conn),
        "password_is_default": db.check_password(
            db.DEFAULT_PASSWORD, user["pwd_hash"], user["pwd_salt"]),
        "people": people,
        "submitted": len(prefs),
        "meta": ({"created_at": meta["created_at"], "created_by": meta["created_by"],
                  "summary": meta["summary"]} if meta else None),
        "report_days": db.report_days(conn),
        "last_reset": db.get_setting(conn, "last_reset"),
        "window": window_payload(conn),
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
            return fail("Пароль должен состоять ровно из 6 символов: "
                        "английские буквы и цифры.")
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
    users_total = len(db.all_users(conn))
    raw_places = data.get("places_count")
    if raw_places not in (None, ""):
        try:
            value = int(raw_places)
        except (TypeError, ValueError):
            return fail("Число мест должно быть целым.")
        if value < users_total:
            return fail("Мест не может быть меньше числа участников (%d)." % users_total)
        db.set_setting(conn, "places_count", value)
        db.log(conn, user["full_name"], "SETTINGS", "количество мест: %d" % value)

    if "test_mode" in data:
        new_mode = "1" if data.get("test_mode") else "0"
        if new_mode != db.get_setting(conn, "test_mode", "0"):
            db.set_setting(conn, "test_mode", new_mode)
            db.log(conn, user["full_name"], "SETTINGS",
                   "режим отладки (круглосуточный доступ): %s"
                   % ("включён" if new_mode == "1" else "выключен"), level="WARNING")
    return jsonify({"ok": True, "message": "Настройки сохранены."})


@app.route("/api/admin/reset", methods=["POST"])
@api_admin_required
def api_admin_reset(conn, user):
    prefs, swaps = db.reset_day(conn, user["full_name"], "ручная подготовка к новому расчёту")
    return jsonify({"ok": True,
                    "message": "Сайт подготовлен к новому дню: стёрто пожеланий %d, "
                               "отменено заявок на обмен %d." % (prefs, swaps)})


@app.route("/api/admin/compute", methods=["POST"])
@api_admin_required
def api_admin_compute(conn, user):
    data = request.get_json(silent=True) or {}
    day = data.get("day") or today_str()
    places = db.places_count(conn)
    participants = [(u["id"], u["full_name"]) for u in db.all_users(conn)]
    prefs = {p["user_id"]: (p["p1"], p["p2"], p["p3"])
             for p in db.all_preferences(conn, day)}

    state, _ = db.window_state(conn)
    if day == today_str() and state == "open":
        db.log(conn, user["full_name"], "CALC_EARLY",
               "расчёт запущен до закрытия приёма пожеланий", level="WARNING", day=day)

    db.log(conn, user["full_name"], "CALC_START",
           "расчёт распределения за %s: участников %d, пожеланий %d, мест %d"
           % (day, len(participants), len(prefs), places), day=day)

    try:
        rows, log_lines, stats = assignment.solve(participants, prefs, places)
    except ValueError as exc:
        db.log(conn, user["full_name"], "CALC_ERROR", str(exc), level="ERROR", day=day)
        return fail("Расчёт невозможен: %s" % exc)

    for line in log_lines:
        if line:
            db.log(conn, "РАСЧЁТ", "CALC", line, day=day)

    summary = ("Учтено пожеланий: %d из %d (1-е: %d, 2-е: %d, 3-е: %d); "
               "не учтено: %d; без пожеланий: %d"
               % (stats["satisfied"], stats["voters"], stats["rank1"], stats["rank2"],
                  stats["rank3"], stats["missed"], stats["silent"]))

    db.save_results(conn, day,
                    [(day, r["user_id"], r["place"], r["status"], r["rank"], r["wishes"])
                     for r in rows], user["full_name"], summary)
    db.log(conn, user["full_name"], "CALC_DONE", summary, day=day)
    return jsonify({"ok": True, "day": day, "summary": summary,
                    "message": "Распределение за %s сформировано." % day})


@app.route("/api/admin/report")
@api_admin_required
def api_admin_report(conn, user):
    day = request.args.get("day") or today_str()
    results = db.get_results(conn, day)
    if not results:
        return fail("За %s распределение ещё не формировалось." % day, 404)
    meta = db.get_report_meta(conn, day)
    return jsonify({
        "day": day,
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
                  for s in db.swaps_for_day(conn, day)],
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

def _report_csv(conn, day):
    results = db.get_results(conn, day)
    meta = db.get_report_meta(conn, day)
    created = meta["created_at"] if meta else db.local_now().strftime("%Y-%m-%d %H:%M:%S")
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Отчёт по очереди сдачи лабораторной работы"])
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


@app.route("/download/report/<day>.csv")
def download_csv(day):
    conn, user = _download_guard()
    try:
        if not db.get_results(conn, day):
            abort(404)
        data = ("﻿" + _report_csv(conn, day)).encode("utf-8")
        db.log(conn, user["full_name"], "EXPORT_CSV", "выгрузка отчёта за %s" % day, day=day)
        return send_file(io.BytesIO(data), mimetype="text/csv; charset=utf-8",
                         as_attachment=True, download_name="Очередь_%s.csv" % day)
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


@app.route("/download/report/<day>.zip")
def download_zip(day):
    conn, user = _download_guard()
    try:
        if not db.get_results(conn, day):
            abort(404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Очередь_%s.csv" % day,
                        ("﻿" + _report_csv(conn, day)).encode("utf-8"))
            zf.writestr("Журнал_%s.log" % day, _report_log(conn, day).encode("utf-8"))
        buf.seek(0)
        db.log(conn, user["full_name"], "EXPORT_ZIP",
               "выгрузка отчёта и журнала за %s" % day, day=day)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name="Отчет_%s.zip" % day)
    finally:
        conn.close()


# ------------------------------------------------- фоновая подготовка в 19:55

def _reset_worker():
    while True:
        wait = min(db.seconds_to_reset(), 300)
        time_mod.sleep(max(wait, 5))
        try:
            conn = get_conn()
            try:
                db.ensure_daily_reset(conn)
            finally:
                conn.close()
        except Exception as exc:                      # noqa: BLE001
            print("Ошибка планировщика сброса: %s" % exc)


def start_scheduler():
    thread = threading.Thread(target=_reset_worker, daemon=True, name="daily-reset")
    thread.start()
    return thread


# --------------------------------------------------------------- старт сервиса

def startup(seed=True, scheduler=True):
    """Готовит приложение к работе: схема, список людей, планировщик сброса.

    Вызывается и при локальном запуске, и под gunicorn на хостинге, где диск
    может быть чистым после каждого перезапуска.
    """
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
