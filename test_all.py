# -*- coding: utf-8 -*-
"""Проверки алгоритма распределения и работы сайта.

Запуск:  python test_all.py
Тесты используют отдельную временную базу и не трогают рабочие данные.
"""

import os
import random
import shutil
import sys
import tempfile

import db

# Отдельная база и отдельный каталог журналов только для тестов.
TMP = tempfile.mkdtemp(prefix="lab_tests_")
db.DB_PATH = os.path.join(TMP, "test.db")
db.LOG_DIR = os.path.join(TMP, "logs")

import assignment           # noqa: E402
import app as web           # noqa: E402

PASSWORDS = {}
FAILURES = []


def check(condition, message):
    if condition:
        print("  ok   %s" % message)
    else:
        print("  FAIL %s" % message)
        FAILURES.append(message)


# ------------------------------------------------------------ алгоритм

def test_algorithm_matches_bruteforce():
    print("\n[1] Венгерский алгоритм совпадает с полным перебором")
    rng = random.Random(20260907)
    worst = 0
    for case in range(25):
        people = 5
        places = rng.choice([5, 6, 7])
        participants = [(i, "Человек %d" % i) for i in range(people)]
        prefs = {}
        for uid, _ in participants:
            if rng.random() < 0.75:
                prefs[uid] = tuple(rng.sample(range(1, places + 1), 3))
        rows, _, _ = assignment.solve(participants, prefs, places, seed=case)
        got = {r["user_id"]: r["place"] for r in rows}
        cost_got = sum(assignment.cell_cost(got[uid], prefs[uid]) for uid in prefs)
        _, cost_best = assignment.solve_bruteforce(participants, prefs, places)
        worst = max(worst, cost_got - cost_best)
        if cost_got != cost_best:
            check(False, "случай %d: стоимость %d вместо оптимальной %d"
                  % (case, cost_got, cost_best))
            return
    check(worst == 0, "25 случайных случаев решены строго оптимально")


def test_all_wishes_satisfied():
    print("\n[2] Непересекающиеся пожелания выполняются полностью")
    participants = [(1, "А"), (2, "Б"), (3, "В")]
    prefs = {1: (1, 2, 3), 2: (2, 3, 1), 3: (3, 1, 2)}
    rows, _, stats = assignment.solve(participants, prefs, 3, seed=1)
    places = sorted(r["place"] for r in rows)
    check(places == [1, 2, 3], "места розданы без дублей")
    check(stats["satisfied"] == 3, "все три пожелания учтены")
    check(stats["rank1"] == 3, "каждый получил первое желание")


def test_conflict_and_nearest():
    print("\n[3] Конфликт: четверо хотят одни и те же три места")
    participants = [(i, "Ч%d" % i) for i in range(1, 5)]
    prefs = {i: (1, 2, 3) for i in range(1, 5)}
    rows, log_lines, stats = assignment.solve(participants, prefs, 4, seed=3)
    by_user = {r["user_id"]: r for r in rows}
    check(stats["satisfied"] == 3, "трое получили желаемое место")
    check(stats["missed"] == 1, "один остался без желаемого места")
    loser = [r for r in rows if r["status"] == "missed"][0]
    check(loser["place"] == 4, "оставшийся поставлен на ближайшее свободное место 4")
    check(any("НЕ УЧТЕНО" in line for line in log_lines),
          "в журнале указано, чьё мнение не учтено")
    check(any("УЧТЕНО" in line for line in log_lines),
          "в журнале указано, чьи пожелания учтены")
    check(len({r["place"] for r in by_user.values()}) == 4, "места не повторяются")


def test_silent_participants_random():
    print("\n[4] Промолчавшие получают оставшиеся места случайно")
    participants = [(i, "Ч%d" % i) for i in range(1, 7)]
    prefs = {1: (1, 2, 3)}
    seen = set()
    for seed in range(12):
        rows, _, stats = assignment.solve(participants, prefs, 6, seed=seed)
        by_user = {r["user_id"]: r["place"] for r in rows}
        check_places = sorted(by_user.values())
        if check_places != [1, 2, 3, 4, 5, 6]:
            check(False, "перестановка мест нарушена при seed=%d" % seed)
            return
        if by_user[1] != 1:
            check(False, "проголосовавший не получил первое желание при seed=%d" % seed)
            return
        seen.add(tuple(by_user[i] for i in range(2, 7)))
    check(len(seen) > 1, "распределение промолчавших действительно случайное")
    check(stats["silent"] == 5, "пятеро посчитаны как безразличные")


# ------------------------------------------------------------ сайт

def setup_site():
    conn = db.connect()
    db.init_schema(conn)
    for full_name, password in db.import_people(conn):
        PASSWORDS[db.login_key(full_name)] = password
    db.set_setting(conn, "test_mode", "1")     # чтобы тесты не зависели от времени
    conn.close()


def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


def login(cli, fio):
    return cli.post("/api/login", json={"fio": fio, "password": PASSWORDS[db.login_key(fio)]})


def test_login_rules():
    print("\n[5] Вход по фамилии, имени и паролю")
    cli = client()
    bad = cli.post("/api/login", json={"fio": "Иванова Анна", "password": "000000"})
    check(bad.status_code == 401, "неверный пароль отклонён")

    ok = login(cli, "Иванова Анна")
    check(ok.status_code == 200, "вход с правильным паролем выполнен")
    check(ok.get_json()["user"]["is_admin"] is False, "обычный пользователь не админ")

    admin = client()
    r = login(admin, "Пархачева Екатерина")
    check(r.get_json()["user"]["is_admin"] is True, "Пархачева Екатерина — администратор")

    check(all(db.PASSWORD_RE.match(p) for p in PASSWORDS.values()),
          "все пароли из 6 символов (буквы и цифры)")


def test_window_closed():
    print("\n[6] Вне окна 20:00-21:00 пускают только администратора")
    original = db.window_state
    db.window_state = lambda conn, now=None: ("closed", "Тест: окно закрыто.")
    try:
        cli = client()
        r = login(cli, "Иванова Анна")
        check(r.status_code == 403, "обычному пользователю вход закрыт")
        adm = client()
        r = login(adm, "Пархачева Екатерина")
        check(r.status_code == 200, "администратор входит в любое время")
        r = adm.get("/api/admin/overview")
        check(r.status_code == 200, "администратор видит отчётный раздел")
    finally:
        db.window_state = original


def test_preferences_flow():
    print("\n[7] Фиксация и редактирование пожеланий")
    cli = client()
    login(cli, "Иванова Анна")

    r = cli.post("/api/user/prefs", json={"p1": 1, "p2": 1, "p3": 2})
    check(r.status_code == 400, "одинаковые места отклонены")

    r = cli.post("/api/user/prefs", json={"p1": 1, "p2": 2, "p3": 999})
    check(r.status_code == 400, "место вне диапазона отклонено")

    r = cli.post("/api/user/prefs", json={"p1": 3, "p2": 4, "p3": 5})
    check(r.status_code == 200, "пожелания зафиксированы")

    state = cli.get("/api/user/state").get_json()
    check(state["pref"] == {"p1": 3, "p2": 4, "p3": 5, "updated_at": state["pref"]["updated_at"]},
          "сохранённые пожелания видны на странице")

    r = cli.post("/api/user/prefs", json={"p1": 7, "p2": 8, "p3": 9})
    check(r.status_code == 200, "пожелания отредактированы")
    check(cli.get("/api/user/state").get_json()["pref"]["p1"] == 7, "изменения сохранились")

    r = cli.delete("/api/user/prefs")
    check(r.status_code == 200 and cli.get("/api/user/state").get_json()["pref"] is None,
          "пожелания можно удалить")

    cli.post("/api/user/prefs", json={"p1": 7, "p2": 8, "p3": 9})


def test_compute_and_report():
    print("\n[8] Расчёт распределения и отчёт администратору")
    conn = db.connect()
    users = {u["full_name"]: u["id"] for u in db.all_users(conn)}
    conn.close()

    for fio, picks in [("Зайцев Никита", (1, 2, 3)), ("Данилов Павел", (1, 2, 3)),
                       ("Отвагин Иван", (2, 3, 4)), ("Цепова Елена", (25, 24, 23))]:
        c = client()
        login(c, fio)
        c.post("/api/user/prefs", json={"p1": picks[0], "p2": picks[1], "p3": picks[2]})

    adm = client()
    login(adm, "Пархачева Екатерина")
    day = web.today_str()
    r = adm.post("/api/admin/compute", json={"day": day})
    check(r.status_code == 200, "расчёт выполнен")

    report = adm.get("/api/admin/report?day=" + day).get_json()
    check(len(report["rows"]) == 26, "в отчёте все 26 участников")
    check(sorted(x["place"] for x in report["rows"]) == list(range(1, 27)),
          "места 1..26 розданы ровно по одному разу")
    check(report["meta"]["created_at"] is not None, "в отчёте есть дата формирования")

    voted = {r_["full_name"]: r_ for r_ in report["rows"] if r_["wishes"]}
    check(len(voted) == 5, "учтены пожелания пяти проголосовавших")
    check(voted["Цепова Елена Дмитриевна"]["place"] == 25,
          "непересекающееся пожелание выполнено точно")

    csv_resp = adm.get("/download/report/%s.csv" % day)
    check(csv_resp.status_code == 200 and "Дата формирования" in csv_resp.data.decode("utf-8"),
          "CSV с фамилиями, местами и датой формирования выгружается")
    zip_resp = adm.get("/download/report/%s.zip" % day)
    check(zip_resp.status_code == 200 and zip_resp.data[:2] == b"PK",
          "ZIP с отчётом и журналом выгружается")
    log_resp = adm.get("/download/log/%s.log" % day)
    text = log_resp.data.decode("utf-8")
    check("CALC_DONE" in text and "УЧТЕНО" in text,
          "в журнале видно, чьё мнение учтено, а чьё нет")

    plain = client()
    check(plain.get("/download/report/%s.csv" % day).status_code in (401, 403),
          "выгрузка недоступна без прав администратора")


def test_swap_flow():
    print("\n[9] Обмен местами между участниками")
    a = client(); login(a, "Зайцев Никита")
    b = client(); login(b, "Отвагин Иван")

    state_a = a.get("/api/user/state").get_json()
    state_b = b.get("/api/user/state").get_json()
    place_a = state_a["my_result"]["place"]
    place_b = state_b["my_result"]["place"]
    b_id = [c["user_id"] for c in state_a["candidates"]
            if c["full_name"] == "Отвагин Иван Александрович"][0]

    r = a.post("/api/user/swaps", json={"to_user_id": b_id})
    check(r.status_code == 200, "заявка на обмен отправлена")
    r = a.post("/api/user/swaps", json={"to_user_id": b_id})
    check(r.status_code == 400, "повторная заявка тому же человеку отклонена")

    incoming = [s for s in b.get("/api/user/state").get_json()["swaps"]
                if s["direction"] == "incoming" and s["status"] == "pending"]
    check(len(incoming) == 1, "получатель видит входящую заявку")
    swap_id = incoming[0]["id"]

    outsider = client(); login(outsider, "Цепова Елена")
    r = outsider.post("/api/user/swaps/%d" % swap_id, json={"action": "accept"})
    check(r.status_code == 403, "посторонний не может ответить на чужую заявку")

    r = b.post("/api/user/swaps/%d" % swap_id, json={"action": "decline"})
    check(r.status_code == 200, "получатель может отказать")
    check(a.get("/api/user/state").get_json()["my_result"]["place"] == place_a,
          "после отказа места не изменились")

    a.post("/api/user/swaps", json={"to_user_id": b_id})
    incoming = [s for s in b.get("/api/user/state").get_json()["swaps"]
                if s["status"] == "pending"]
    swap_id = incoming[0]["id"]
    r = b.post("/api/user/swaps/%d" % swap_id, json={"action": "accept"})
    check(r.status_code == 200, "получатель может согласиться")

    check(a.get("/api/user/state").get_json()["my_result"]["place"] == place_b,
          "инициатор получил место собеседника")
    check(b.get("/api/user/state").get_json()["my_result"]["place"] == place_a,
          "собеседник получил место инициатора")

    adm = client(); login(adm, "Пархачева Екатерина")
    report = adm.get("/api/admin/report?day=" + web.today_str()).get_json()
    check(sorted(x["place"] for x in report["rows"]) == list(range(1, 27)),
          "после обмена места по-прежнему уникальны")
    check(any(x["swapped"] for x in report["rows"]), "в отчёте отмечен факт обмена")
    check(any(s["status"] == "accepted" for s in report["swaps"]),
          "история заявок попала в отчёт")


def test_daily_reset():
    print("\n[10] Подготовка к новому дню в 19:55")
    conn = db.connect()
    before = len(db.all_preferences(conn, web.today_str()))
    conn.close()
    check(before > 0, "перед сбросом пожелания есть")

    adm = client(); login(adm, "Пархачева Екатерина")
    r = adm.post("/api/admin/reset")
    check(r.status_code == 200, "ручная подготовка выполняется")

    conn = db.connect()
    check(len(db.all_preferences(conn, web.today_str())) == 0, "все пожелания стёрты")
    pending = conn.execute("SELECT COUNT(*) c FROM swaps WHERE status='pending'").fetchone()["c"]
    check(pending == 0, "незакрытые заявки на обмен отменены")
    check(len(db.get_results(conn, web.today_str())) == 26,
          "готовое распределение и отчёт сохранены")
    events = db.read_events(conn, web.today_str(), web.today_str())
    check(any(e["action"] == "RESET" for e in events), "сброс записан в журнал")

    # автоматический сброс: помечаем, что сегодня его ещё не было
    db.set_setting(conn, "last_reset", "2000-01-01")
    conn.close()
    from datetime import datetime as dt
    conn = db.connect()
    fired = db.ensure_daily_reset(conn, dt.combine(dt.now().date(), db.RESET_AT))
    check(fired is True, "автоматический сброс срабатывает в 19:55")
    again = db.ensure_daily_reset(conn, dt.combine(dt.now().date(), db.RESET_AT))
    check(again is False, "повторно за день сброс не выполняется")
    early = db.ensure_daily_reset(conn, dt.combine(dt.now().date(), db.OPEN_FROM).replace(hour=10))
    check(early is False, "до 19:55 сброс не выполняется")
    conn.close()


def test_admin_password_change():
    print("\n[11] Администратор меняет пароль пользователю")
    adm = client(); login(adm, "Пархачева Екатерина")
    conn = db.connect()
    target = db.get_user_by_login(conn, "Пуртова Софья")
    conn.close()

    r = adm.post("/api/admin/password", json={"user_id": target["id"], "mode": "manual",
                                              "password": "short"})
    check(r.status_code == 400, "пароль неверной длины отклонён")
    r = adm.post("/api/admin/password", json={"user_id": target["id"], "mode": "manual",
                                              "password": "ab12CD"})
    check(r.status_code == 200, "пароль задан вручную")

    cli = client()
    r = cli.post("/api/login", json={"fio": "Пуртова Софья", "password": "ab12CD"})
    check(r.status_code == 200, "вход с новым паролем работает")

    r = adm.post("/api/admin/password", json={"user_id": target["id"], "mode": "generate"})
    generated = r.get_json()["password"]
    check(db.PASSWORD_RE.match(generated) is not None, "сгенерирован пароль из 6 символов")
    cli = client()
    check(cli.post("/api/login", json={"fio": "Пуртова Софья",
                                       "password": generated}).status_code == 200,
          "вход со сгенерированным паролем работает")
    old = client()
    check(old.post("/api/login", json={"fio": "Пуртова Софья",
                                       "password": "ab12CD"}).status_code == 401,
          "старый пароль больше не подходит")

    plain = client(); login(plain, "Иванова Анна")
    check(plain.post("/api/admin/password",
                     json={"user_id": target["id"], "mode": "generate"}).status_code == 403,
          "обычный пользователь не может менять пароли")


def main():
    setup_site()
    for test in [test_algorithm_matches_bruteforce, test_all_wishes_satisfied,
                 test_conflict_and_nearest, test_silent_participants_random,
                 test_login_rules, test_window_closed, test_preferences_flow,
                 test_compute_and_report, test_swap_flow, test_daily_reset,
                 test_admin_password_change]:
        test()

    print("\n" + "=" * 60)
    if FAILURES:
        print("ПРОВАЛЕНО проверок: %d" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
    else:
        print("Все проверки пройдены.")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
