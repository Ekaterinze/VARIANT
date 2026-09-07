# -*- coding: utf-8 -*-
"""Проверки алгоритма распределения и работы сайта.

Запуск:  python test_all.py
Тесты используют отдельную временную базу и не трогают рабочие данные.
"""

import io
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

# Тесты сами заводят людей и не нуждаются в фоновом планировщике.
os.environ["AUTO_SEED"] = "0"
os.environ["RUN_SCHEDULER"] = "0"

import assignment           # noqa: E402
import app as web           # noqa: E402

PASSWORDS = {}
FAILURES = []
MAIN_LIST = {"id": None}


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
        order = {uid: i for i, uid in enumerate(rng.sample(sorted(prefs), len(prefs)))}
        rows, _, _ = assignment.solve(participants, prefs, places, seed=case, order=order)
        got = {r["user_id"]: r["place"] for r in rows}
        voters = [uid for uid, _ in participants if uid in prefs]
        weights = dict(zip(voters, assignment.vote_weights(
            len(voters), [order[uid] for uid in voters])))
        scale = assignment.tie_scale(len(voters), places)
        cost_got = sum(assignment.cell_cost(got[uid], prefs[uid]) * (scale + weights[uid])
                       for uid in prefs)
        _, cost_best = assignment.solve_bruteforce(participants, prefs, places, order)
        worst = max(worst, cost_got - cost_best)
        if cost_got != cost_best:
            check(False, "случай %d: стоимость %d вместо оптимальной %d"
                  % (case, cost_got, cost_best))
            return
    check(worst == 0, "25 случайных случаев решены строго оптимально (с учётом очереди голосования)")


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
        if sorted(by_user.values()) != [1, 2, 3, 4, 5, 6]:
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
    MAIN_LIST["id"] = db.ensure_default_list(conn)
    db.set_setting(conn, "test_mode", "1")     # чтобы тесты не зависели от времени
    conn.close()


def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


def login(cli, fio):
    return cli.post("/api/login", json={"fio": fio, "password": PASSWORDS[db.login_key(fio)]})


def user_list(cli, list_id=None):
    """Один лист со страницы участника."""
    lists = cli.get("/api/user/state").get_json()["lists"]
    if list_id is None:
        return lists[0]
    return [x for x in lists if x["id"] == list_id][0]


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
          "все пароли из 7 символов (буквы и цифры)")


def test_window_closed():
    print("\n[6] Вне окна приёма: вход открыт, пожелания не принимаются")
    original = db.list_window
    db.list_window = lambda conn, lst, now=None: ("closed", "Тест: приём закрыт.")
    try:
        cli = client()
        r = login(cli, "Иванова Анна")
        check(r.status_code == 200, "обычный пользователь входит в любое время")

        lst = user_list(cli)
        check(lst["can_edit"] is False, "поля пожеланий помечены как недоступные")

        r = cli.post("/api/user/prefs", json={"list_id": lst["id"], "p1": 1, "p2": 2, "p3": 3})
        check(r.status_code == 403, "новые пожелания вне окна не принимаются")
        check("принимаются" in r.get_json()["error"],
              "в ответе указано расписание приёма")

        r = cli.delete("/api/user/prefs", json={"list_id": lst["id"]})
        check(r.status_code == 403, "удалить пожелания вне окна тоже нельзя")

        adm = client()
        check(login(adm, "Пархачева Екатерина").status_code == 200,
              "администратор входит в любое время")
        check(adm.get("/api/admin/lists").status_code == 200,
              "администратор видит раздел листов")
        check(adm.post("/api/user/prefs",
                       json={"list_id": lst["id"], "p1": 1, "p2": 2, "p3": 3}
                       ).status_code == 403,
              "администратору вне окна пожелания тоже не принимаются")
    finally:
        db.list_window = original


def test_preferences_flow():
    print("\n[7] Фиксация и редактирование пожеланий")
    cli = client()
    login(cli, "Иванова Анна")
    lid = MAIN_LIST["id"]

    r = cli.post("/api/user/prefs", json={"list_id": lid, "p1": 1, "p2": 1, "p3": 2})
    check(r.status_code == 400, "одинаковые места отклонены")

    r = cli.post("/api/user/prefs", json={"list_id": lid, "p1": 1, "p2": 2, "p3": 999})
    check(r.status_code == 400, "место вне диапазона отклонено")

    r = cli.post("/api/user/prefs", json={"p1": 1, "p2": 2, "p3": 3})
    check(r.status_code == 400, "без указания листа пожелания не принимаются")

    r = cli.post("/api/user/prefs", json={"list_id": lid, "p1": 3, "p2": 4, "p3": 5})
    check(r.status_code == 200, "пожелания зафиксированы")

    lst = user_list(cli, lid)
    check(lst["pref"]["p1"] == 3 and lst["pref"]["p3"] == 5,
          "сохранённые пожелания видны на странице")
    check(any(v["is_me"] for v in lst["voted"]),
          "участник видит себя в списке проголосовавших")

    r = cli.post("/api/user/prefs", json={"list_id": lid, "p1": 7, "p2": 8, "p3": 9})
    check(r.status_code == 200 and user_list(cli, lid)["pref"]["p1"] == 7,
          "пожелания отредактированы")

    r = cli.delete("/api/user/prefs", json={"list_id": lid})
    check(r.status_code == 200 and user_list(cli, lid)["pref"] is None,
          "пожелания можно удалить")

    cli.post("/api/user/prefs", json={"list_id": lid, "p1": 7, "p2": 8, "p3": 9})


def test_compute_and_report():
    print("\n[8] Расчёт распределения и отчёт администратору")
    lid = MAIN_LIST["id"]
    for fio, picks in [("Зайцев Никита", (1, 2, 3)), ("Данилов Павел", (1, 2, 3)),
                       ("Отвагин Иван", (2, 3, 4)), ("Цепова Елена", (25, 24, 23))]:
        c = client()
        login(c, fio)
        c.post("/api/user/prefs", json={"list_id": lid, "p1": picks[0],
                                        "p2": picks[1], "p3": picks[2]})

    adm = client()
    login(adm, "Пархачева Екатерина")
    day = web.today_str()
    r = adm.post("/api/admin/compute", json={"list_id": lid, "day": day})
    check(r.status_code == 200, "расчёт выполнен")

    report = adm.get("/api/admin/report?list_id=%d&day=%s" % (lid, day)).get_json()
    check(len(report["rows"]) == 26, "в отчёте все 26 участников листа")
    check(sorted(x["place"] for x in report["rows"]) == list(range(1, 27)),
          "места 1..26 розданы ровно по одному разу")
    check(report["meta"]["created_at"] is not None, "в отчёте есть дата формирования")
    check(report["list"]["name"] == db.DEFAULT_LIST_NAME, "в отчёте указано название листа")

    voted = {r_["full_name"]: r_ for r_ in report["rows"] if r_["wishes"]}
    check(len(voted) == 5, "учтены пожелания пяти проголосовавших")
    check(voted["Цепова Елена Дмитриевна"]["place"] == 25,
          "непересекающееся пожелание выполнено точно")

    csv_resp = adm.get("/download/report/%d/%s.csv" % (lid, day))
    text = csv_resp.data.decode("utf-8")
    check(csv_resp.status_code == 200 and "Дата формирования" in text and "Лист" in text,
          "CSV с названием листа, фамилиями, местами и датой выгружается")
    zip_resp = adm.get("/download/report/%d/%s.zip" % (lid, day))
    check(zip_resp.status_code == 200 and zip_resp.data[:2] == b"PK",
          "ZIP с отчётом и журналом выгружается")
    log_text = adm.get("/download/log/%s.log" % day).data.decode("utf-8")
    check("CALC_DONE" in log_text and "УЧТЕНО" in log_text,
          "в журнале видно, чьё мнение учтено, а чьё нет")

    plain = client()
    check(plain.get("/download/report/%d/%s.csv" % (lid, day)).status_code in (401, 403),
          "выгрузка недоступна без прав администратора")


def test_queue_visible_to_users():
    print("\n[9] Участники видят очередь и кто как в неё попал")
    cli = client()
    login(cli, "Иванова Анна")
    lst = user_list(cli, MAIN_LIST["id"])

    check(len(lst["queue"]) == 26, "в очереди показаны все 26 человек")
    check([q["place"] for q in lst["queue"]] == list(range(1, 27)),
          "очередь отсортирована по местам")
    check(sum(1 for q in lst["queue"] if q["voted"]) == 5,
          "видно, что голосовали пятеро")
    check(sum(1 for q in lst["queue"] if not q["voted"]) == 21,
          "остальные помечены как назначенные автоматически")
    auto = [q for q in lst["queue"] if not q["voted"]][0]
    check(auto["status_text"] == "не голосовал, место назначено случайно",
          "у назначенных автоматически понятная подпись")
    check(any(q["is_me"] for q in lst["queue"]), "участник видит себя в очереди")
    check(len(lst["not_voted"]) + len(lst["voted"]) == 26,
          "списки проголосовавших и молчунов покрывают весь лист")


def test_swap_flow():
    print("\n[10] Обмен местами между участниками")
    lid = MAIN_LIST["id"]
    a = client(); login(a, "Зайцев Никита")
    b = client(); login(b, "Отвагин Иван")

    place_a = user_list(a, lid)["my_result"]["place"]
    place_b = user_list(b, lid)["my_result"]["place"]
    b_id = [c["user_id"] for c in user_list(a, lid)["candidates"]
            if c["full_name"] == "Отвагин Иван Александрович"][0]

    r = a.post("/api/user/swaps", json={"list_id": lid, "to_user_id": b_id})
    check(r.status_code == 200, "заявка на обмен отправлена")
    r = a.post("/api/user/swaps", json={"list_id": lid, "to_user_id": b_id})
    check(r.status_code == 400, "повторная заявка тому же человеку отклонена")

    incoming = [s for s in user_list(b, lid)["swaps"]
                if s["direction"] == "incoming" and s["status"] == "pending"]
    check(len(incoming) == 1, "получатель видит входящую заявку")
    swap_id = incoming[0]["id"]

    outsider = client(); login(outsider, "Цепова Елена")
    r = outsider.post("/api/user/swaps/%d" % swap_id, json={"action": "accept"})
    check(r.status_code == 403, "посторонний не может ответить на чужую заявку")

    r = b.post("/api/user/swaps/%d" % swap_id, json={"action": "decline"})
    check(r.status_code == 200, "получатель может отказать")
    check(user_list(a, lid)["my_result"]["place"] == place_a,
          "после отказа места не изменились")

    a.post("/api/user/swaps", json={"list_id": lid, "to_user_id": b_id})
    swap_id = [s for s in user_list(b, lid)["swaps"] if s["status"] == "pending"][0]["id"]
    r = b.post("/api/user/swaps/%d" % swap_id, json={"action": "accept"})
    check(r.status_code == 200, "получатель может согласиться")

    check(user_list(a, lid)["my_result"]["place"] == place_b,
          "инициатор получил место собеседника")
    check(user_list(b, lid)["my_result"]["place"] == place_a,
          "собеседник получил место инициатора")

    adm = client(); login(adm, "Пархачева Екатерина")
    report = adm.get("/api/admin/report?list_id=%d&day=%s"
                     % (lid, web.today_str())).get_json()
    check(sorted(x["place"] for x in report["rows"]) == list(range(1, 27)),
          "после обмена места по-прежнему уникальны")
    check(any(x["swapped"] for x in report["rows"]), "в отчёте отмечен факт обмена")
    check(any(s["status"] == "accepted" for s in report["swaps"]),
          "история заявок попала в отчёт")

    original = db.list_window
    db.list_window = lambda conn, lst, now=None: ("closed", "Тест: приём закрыт.")
    try:
        r = a.post("/api/user/swaps", json={"list_id": lid, "to_user_id": b_id})
        check(r.status_code == 200,
              "договориться об обмене можно и вне окна приёма пожеланий")
        pending = [s for s in user_list(b, lid)["swaps"] if s["status"] == "pending"]
        check(len(pending) == 1, "заявка видна получателю вне окна")
        check(b.post("/api/user/swaps/%d" % pending[0]["id"],
                     json={"action": "decline"}).status_code == 200,
              "ответить на заявку вне окна тоже можно")
    finally:
        db.list_window = original


def test_daily_reset():
    print("\n[11] Подготовка листа к новому приёму")
    lid = MAIN_LIST["id"]
    conn = db.connect()
    before = len(db.all_preferences(conn, lid, web.today_str()))
    conn.close()
    check(before > 0, "перед подготовкой пожелания есть")

    adm = client(); login(adm, "Пархачева Екатерина")
    r = adm.post("/api/admin/reset", json={"list_id": lid})
    check(r.status_code == 200, "ручная подготовка выполняется")

    conn = db.connect()
    check(len(db.all_preferences(conn, lid, web.today_str())) == 0, "все пожелания стёрты")
    pending = conn.execute(
        "SELECT COUNT(*) c FROM swaps WHERE status='pending'").fetchone()["c"]
    check(pending == 0, "незакрытые заявки на обмен отменены")
    check(len(db.get_results(conn, lid, web.today_str())) == 26,
          "готовое распределение и отчёт сохранены")
    events = db.read_events(conn, web.today_str(), web.today_str())
    check(any(e["action"] == "RESET" for e in events), "подготовка записана в журнал")

    # автоматическая подготовка за 5 минут до открытия приёма
    from datetime import datetime as dt, timedelta
    lst = db.get_list(conn, lid)
    db.set_setting(conn, "last_reset_%d" % lid, "2000-01-01")
    moment = db.list_reset_moment(lst, dt.now().date())
    check(db.ensure_list_resets(conn, moment) == [lst["name"]],
          "автоматическая подготовка срабатывает за %d мин до открытия"
          % db.RESET_LEAD_MINUTES)
    check(db.ensure_list_resets(conn, moment) == [],
          "повторно за день подготовка не выполняется")
    db.set_setting(conn, "last_reset_%d" % lid, "2000-01-01")
    check(db.ensure_list_resets(conn, moment - timedelta(minutes=10)) == [],
          "раньше времени подготовка не выполняется")

    # запоздалая автоподготовка (сервер был выключен) не должна стирать
    # пожелания текущего круга — только прошлые
    today = web.today_str()
    yesterday = (dt.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    user_id = db.get_user_by_login(conn, "Иванова Анна")["id"]
    db.save_preference(conn, lid, yesterday, user_id, 1, 2, 3)
    db.save_preference(conn, lid, today, user_id, 4, 5, 6)
    db.set_setting(conn, "last_reset_%d" % lid, "2000-01-01")
    db.ensure_list_resets(conn, moment)
    check(db.get_preference(conn, lid, today, user_id) is not None,
          "сегодняшние пожелания переживают запоздалую автоподготовку")
    check(db.get_preference(conn, lid, yesterday, user_id) is None,
          "пожелания прошлых дней стираются")

    db.reset_list(conn, lst, "тест", "ручная подготовка", include_today=True)
    check(db.get_preference(conn, lid, today, user_id) is None,
          "ручная подготовка администратора стирает и сегодняшние пожелания")
    conn.close()


def test_admin_password_change():
    print("\n[12] Администратор меняет пароль пользователю")
    adm = client(); login(adm, "Пархачева Екатерина")
    conn = db.connect()
    target = db.get_user_by_login(conn, "Пуртова Софья")
    conn.close()

    r = adm.post("/api/admin/password", json={"user_id": target["id"], "mode": "manual",
                                              "password": "short"})
    check(r.status_code == 400, "пароль неверной длины отклонён")
    r = adm.post("/api/admin/password", json={"user_id": target["id"], "mode": "manual",
                                              "password": "ab12CD7"})
    check(r.status_code == 200, "пароль задан вручную")

    cli = client()
    r = cli.post("/api/login", json={"fio": "Пуртова Софья", "password": "ab12CD7"})
    check(r.status_code == 200, "вход с новым паролем работает")

    r = adm.post("/api/admin/password", json={"user_id": target["id"], "mode": "generate"})
    generated = r.get_json()["password"]
    check(db.PASSWORD_RE.match(generated) is not None, "сгенерирован пароль из 7 символов")
    cli = client()
    check(cli.post("/api/login", json={"fio": "Пуртова Софья",
                                       "password": generated}).status_code == 200,
          "вход со сгенерированным паролем работает")
    old = client()
    check(old.post("/api/login", json={"fio": "Пуртова Софья",
                                       "password": "ab12CD7"}).status_code == 401,
          "старый пароль больше не подходит")

    plain = client(); login(plain, "Иванова Анна")
    check(plain.post("/api/admin/password",
                     json={"user_id": target["id"], "mode": "generate"}).status_code == 403,
          "обычный пользователь не может менять пароли")


def test_self_password_change():
    print("\n[13] Участник сам меняет себе пароль")
    cli = client()
    login(cli, "Макеев Артём")
    check(cli.get("/api/user/state").get_json()["password_is_default"] is True,
          "сайт видит, что пароль ещё стартовый")

    r = cli.post("/api/user/password", json={"old_password": "0000000",
                                             "new_password": "qwe1234",
                                             "new_password2": "qwe1234"})
    check(r.status_code == 403, "неверный старый пароль отклонён")

    r = cli.post("/api/user/password", json={"old_password": db.DEFAULT_PASSWORD,
                                             "new_password": "qwe1234",
                                             "new_password2": "qwe1235"})
    check(r.status_code == 400, "несовпадение двух новых паролей отклонено")

    r = cli.post("/api/user/password", json={"old_password": db.DEFAULT_PASSWORD,
                                             "new_password": "qwe",
                                             "new_password2": "qwe"})
    check(r.status_code == 400, "короткий новый пароль отклонён")

    r = cli.post("/api/user/password", json={"old_password": db.DEFAULT_PASSWORD,
                                             "new_password": "парол12",
                                             "new_password2": "парол12"})
    check(r.status_code == 400, "русские буквы в пароле отклонены")

    r = cli.post("/api/user/password", json={"old_password": db.DEFAULT_PASSWORD,
                                             "new_password": db.DEFAULT_PASSWORD,
                                             "new_password2": db.DEFAULT_PASSWORD})
    check(r.status_code == 400, "новый пароль не может совпадать со старым")

    r = cli.post("/api/user/password", json={"old_password": db.DEFAULT_PASSWORD,
                                             "new_password": "qwe1234",
                                             "new_password2": "qwe1234"})
    check(r.status_code == 200, "пароль успешно изменён")
    check(cli.get("/api/user/state").get_json()["password_is_default"] is False,
          "предупреждение о стартовом пароле пропало")

    fresh = client()
    check(fresh.post("/api/login", json={"fio": "Макеев Артём",
                                         "password": "qwe1234"}).status_code == 200,
          "вход с новым паролем работает")
    old = client()
    check(old.post("/api/login", json={"fio": "Макеев Артём",
                                       "password": db.DEFAULT_PASSWORD}).status_code == 401,
          "старый пароль больше не подходит")

    conn = db.connect()
    events = db.read_events(conn, web.today_str(), web.today_str())
    conn.close()
    check(any(e["action"] == "PASSWORD_SELF_CHANGE" for e in events),
          "смена пароля записана в журнал")
    check(any(e["action"] == "PASSWORD_SELF_FAIL" for e in events),
          "неудачная попытка тоже записана в журнал")

    anon = client()
    check(anon.post("/api/user/password", json={"old_password": db.DEFAULT_PASSWORD,
                                                "new_password": "qwe1234",
                                                "new_password2": "qwe1234"}
                    ).status_code == 401,
          "без входа пароль сменить нельзя")


def test_timezone_and_seed():
    print("\n[14] Часовой пояс расписания и стартовый пароль")
    check(db.TZ_NAME == os.environ.get("APP_TZ", "Europe/Moscow"),
          "расписание считается в поясе %s, а не по времени сервера" % db.TZ_NAME)
    check(db.PASSWORD_RE.match(db.DEFAULT_PASSWORD) is not None,
          "стартовый пароль '%s' подходит под правило из %d символов"
          % (db.DEFAULT_PASSWORD, db.PASSWORD_LEN))

    fresh = os.path.join(TMP, "fresh.db")
    old_path, db.DB_PATH = db.DB_PATH, fresh
    try:
        conn = db.connect()
        db.init_schema(conn)
        created = dict(db.ensure_seeded(conn))
        check(len(created) == 26, "на пустой базе заводятся все 26 человек")
        check(set(created.values()) == {db.DEFAULT_PASSWORD},
              "после пересоздания базы пароль у всех снова стартовый")
        lists = db.all_lists(conn)
        check(len(lists) == 1 and lists[0]["name"] == db.DEFAULT_LIST_NAME,
              "на пустой базе создаётся лист по умолчанию")
        check(len(db.list_member_ids(conn, lists[0]["id"])) == 26,
              "в лист по умолчанию попадают все люди")
        check(db.ensure_seeded(conn) == [], "повторный запуск никого не дублирует")
        conn.close()
    finally:
        db.DB_PATH = old_path


def test_postgres_layer():
    print("\n[15] Слой совместимости с Postgres")
    check(db.USE_POSTGRES is False, "без DATABASE_URL работает локальный SQLite")
    check(db._pg_sql("SELECT * FROM t WHERE a = ? AND b = ?")
          == "SELECT * FROM t WHERE a = %s AND b = %s",
          "подстановки ? переводятся в %s")

    schema = db.pg_schema()
    check("AUTOINCREMENT" not in schema, "в схеме для Postgres нет AUTOINCREMENT")
    check(schema.count("SERIAL PRIMARY KEY") == 4,
          "все автонумеруемые таблицы получили SERIAL")

    import re as _re
    source = open("db.py", encoding="utf-8").read()
    literals = _re.findall(r"'[^'\n]*'", source)
    check(not [t for t in literals if "?" in t],
          "в текстовых константах запросов нет знаков вопроса")

    class FakeCursor:
        def __init__(self):
            self.many = None

        def execute(self, sql, params=None):
            self.sql, self.params = sql, params

        def executemany(self, sql, rows):
            self.sql, self.many = sql, rows

    class FakeRaw:
        def __init__(self):
            self.cur = FakeCursor()
            self.committed = False
            self.closed = False

        def execute(self, sql, params=None):
            self.sql, self.params = sql, params
            return self.cur

        def cursor(self):
            return self.cur

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    raw = FakeRaw()
    conn = db.PgConnection(raw)
    conn.execute("SELECT value FROM settings WHERE key = ?", ("test_mode",))
    check(raw.sql == "SELECT value FROM settings WHERE key = %s",
          "обёртка переводит запрос перед отправкой в Postgres")
    check(raw.params == ("test_mode",), "параметры передаются кортежем")

    conn.executemany("INSERT INTO results VALUES(?,?)", [["a", 1], ["b", 2]])
    check(raw.cur.many == [("a", 1), ("b", 2)],
          "пакетная вставка приводит строки к кортежам")

    conn.commit(); conn.close()
    check(raw.committed and raw.closed, "commit и close доходят до соединения")


def test_admin_is_participant():
    print("\n[16] Администратор тоже участвует в распределении")
    lid = MAIN_LIST["id"]
    adm = client()
    login(adm, "Пархачева Екатерина")

    r = adm.post("/api/user/prefs", json={"list_id": lid, "p1": 14, "p2": 15, "p3": 16})
    check(r.status_code == 200, "администратор может отправить свои пожелания")
    check(user_list(adm, lid)["pref"]["p1"] == 14, "пожелания администратора сохранены")

    day = web.today_str()
    check(adm.post("/api/admin/compute",
                   json={"list_id": lid, "day": day}).status_code == 200,
          "расчёт выполняется")
    report = adm.get("/api/admin/report?list_id=%d&day=%s" % (lid, day)).get_json()
    mine = [r_ for r_ in report["rows"]
            if r_["full_name"] == "Пархачева Екатерина Евгеньевна"][0]
    check(mine["place"] in (14, 15, 16), "администратор получил одно из желаемых мест")
    check(mine["status"] == "satisfied", "его пожелание учтено наравне с остальными")
    check(user_list(adm, lid)["my_result"]["place"] == mine["place"],
          "своё место администратор видит на странице пожеланий")


# ------------------------------------------------------- листы голосования

def test_lists_crud():
    print("\n[17] Администратор заводит листы с расписанием и составом")
    adm = client(); login(adm, "Пархачева Екатерина")
    data = adm.get("/api/admin/lists").get_json()
    people = data["users"]
    check(len(data["lists"]) == 1, "изначально есть один лист по умолчанию")

    members = [u["id"] for u in people[:6]]
    r = adm.post("/api/admin/lists", json={
        "name": "Лабораторная №2", "description": "Вторая подгруппа, аудитория 314",
        "weekdays": "24", "open_from": "18:30", "open_to": "19:15",
        "places": 0, "active": True, "member_ids": members})
    check(r.status_code == 200, "лист создан")
    list_id = r.get_json()["list_id"]

    r = adm.post("/api/admin/lists", json={"name": "", "member_ids": members})
    check(r.status_code == 400, "лист без названия отклонён")
    r = adm.post("/api/admin/lists", json={"name": "Плохое время", "open_from": "21:00",
                                           "open_to": "20:00", "member_ids": members})
    check(r.status_code == 400, "конец приёма раньше начала отклонён")
    r = adm.post("/api/admin/lists", json={"name": "Мало мест", "places": 2,
                                           "member_ids": members})
    check(r.status_code == 400, "мест меньше, чем участников, — отклонено")

    data = adm.get("/api/admin/lists").get_json()
    created = [x for x in data["lists"] if x["id"] == list_id][0]
    check(created["members_total"] == 6, "в листе шесть участников")
    check(created["places"] == 6, "мест по числу участников")
    check(created["schedule_text"] == "по Вт, Чт с 18:30 до 19:15",
          "расписание показано понятным текстом: %s" % created["schedule_text"])

    r = adm.post("/api/admin/lists/%d" % list_id, json={
        "name": "Лабораторная №2", "description": "Изменённое описание",
        "weekdays": "135", "open_from": "19:00", "open_to": "20:00",
        "places": 10, "active": True, "member_ids": members[:4]})
    check(r.status_code == 200, "лист изменён")
    data = adm.get("/api/admin/lists").get_json()
    edited = [x for x in data["lists"] if x["id"] == list_id][0]
    check(edited["members_total"] == 4, "состав листа обновлён")
    check(edited["places"] == 10, "число мест задано вручную")
    check(edited["weekdays"] == "135", "дни недели обновлены")

    plain = client(); login(plain, "Иванова Анна")
    check(plain.post("/api/admin/lists", json={"name": "Чужой"}).status_code == 403,
          "обычный пользователь не может создавать листы")
    check(plain.get("/api/admin/lists").status_code == 403,
          "и не видит раздел листов")

    return list_id


def test_multiple_lists_same_day():
    print("\n[18] Несколько листов в один день на одной странице")
    adm = client(); login(adm, "Пархачева Екатерина")
    conn = db.connect()
    anna = db.get_user_by_login(conn, "Иванова Анна")
    others = [u["id"] for u in db.all_users(conn)][:8]
    conn.close()
    if anna["id"] not in others:
        others.append(anna["id"])

    today = db.local_now().isoweekday()
    r = adm.post("/api/admin/lists", json={
        "name": "Утренний лист", "description": "Сдаём практику",
        "weekdays": str(today), "open_from": "09:00", "open_to": "10:00",
        "places": 0, "active": True, "member_ids": others})
    morning = r.get_json()["list_id"]
    r = adm.post("/api/admin/lists", json={
        "name": "Вечерний лист", "description": "Сдаём лабораторную",
        "weekdays": str(today), "open_from": "20:00", "open_to": "21:00",
        "places": 0, "active": True, "member_ids": others})
    evening = r.get_json()["list_id"]

    cli = client(); login(cli, "Иванова Анна")
    lists = cli.get("/api/user/state").get_json()["lists"]
    names = [x["name"] for x in lists if x["scheduled_today"]]
    check("Утренний лист" in names and "Вечерний лист" in names,
          "оба сегодняшних листа показаны на одной странице")
    check(names.index("Утренний лист") < names.index("Вечерний лист"),
          "листы идут по времени открытия приёма")

    morning_card = [x for x in lists if x["id"] == morning][0]
    check(morning_card["description"] == "Сдаём практику",
          "у листа показано его описание")
    check(morning_card["places"] == len(others),
          "у каждого листа своё количество мест")

    # у каждого листа свои пожелания
    check(cli.post("/api/user/prefs",
                   json={"list_id": morning, "p1": 1, "p2": 2, "p3": 3}).status_code == 200,
          "в первый лист пожелания принимаются")
    check(cli.post("/api/user/prefs",
                   json={"list_id": evening, "p1": 5, "p2": 6, "p3": 7}).status_code == 200,
          "во второй лист — свои пожелания")
    check(user_list(cli, morning)["pref"]["p1"] == 1 and
          user_list(cli, evening)["pref"]["p1"] == 5,
          "пожелания листов не путаются между собой")

    # расчёт по каждому листу отдельно
    day = web.today_str()
    check(adm.post("/api/admin/compute",
                   json={"list_id": morning, "day": day}).status_code == 200,
          "первый лист посчитан")
    check(adm.post("/api/admin/compute",
                   json={"list_id": evening, "day": day}).status_code == 200,
          "второй лист посчитан")
    m_rows = adm.get("/api/admin/report?list_id=%d&day=%s" % (morning, day)).get_json()["rows"]
    e_rows = adm.get("/api/admin/report?list_id=%d&day=%s" % (evening, day)).get_json()["rows"]
    check(len(m_rows) == len(others) and len(e_rows) == len(others),
          "в каждом отчёте только участники своего листа")
    check([x["place"] for x in m_rows] == list(range(1, len(others) + 1)),
          "очередь первого листа независима от второго")

    conn = db.connect()
    member_names = {u["full_name"] for u in db.list_member_rows(conn, morning)}
    everyone = {u["full_name"] for u in db.all_users(conn)}
    conn.close()
    outsiders = everyone - member_names
    check(outsiders, "в листе состоят не все — есть с кем сравнить")
    check(not (outsiders & {x["full_name"] for x in m_rows}),
          "люди вне листа в его очередь не попали")

    stranger_name = sorted(outsiders)[0]
    stranger = client(); login(stranger, stranger_name)
    ids = [x["id"] for x in stranger.get("/api/user/state").get_json()["lists"]]
    check(morning not in ids, "человек не видит лист, в который не входит")
    check(stranger.post("/api/user/prefs",
                        json={"list_id": morning, "p1": 1, "p2": 2, "p3": 3}
                        ).status_code == 403,
          "и не может в него проголосовать")

    # удаление листа уносит его данные
    check(adm.post("/api/admin/lists/%d/delete" % morning).status_code == 200,
          "лист удаляется")
    conn = db.connect()
    check(len(db.get_results(conn, morning, day)) == 0,
          "вместе с листом удалено его распределение")
    check(db.get_list(conn, morning) is None, "лист исчез из базы")
    check(db.get_list(conn, evening) is not None, "второй лист не задет")
    conn.close()
    check(adm.get("/api/admin/report?list_id=%d&day=%s"
                  % (evening, day)).status_code == 200,
          "отчёт второго листа на месте")


def test_per_list_schedule():
    print("\n[19] У каждого листа своё расписание приёма")
    conn = db.connect()
    db.set_setting(conn, "test_mode", "0")     # проверяем настоящее расписание
    try:
        from datetime import datetime as dt
        monday_evening = dt(2026, 9, 7, 20, 30)      # понедельник
        tuesday_evening = dt(2026, 9, 8, 20, 30)     # вторник
        monday_morning = dt(2026, 9, 7, 9, 30)

        lst = {"id": 999, "name": "Тест", "description": "", "weekdays": "1",
               "open_from": "20:00", "open_to": "21:00", "places": 0, "active": 1}

        state, hint = db.list_window(conn, lst, monday_evening)
        check(state == "open", "в свой день и час приём открыт")
        state, hint = db.list_window(conn, lst, tuesday_evening)
        check(state == "closed", "в другой день недели приём закрыт")
        check("07.09" in hint or "понедельник" in hint.lower() or "Пн" in hint,
              "подсказка называет дату следующего открытия: %s" % hint)
        state, _ = db.list_window(conn, lst, monday_morning)
        check(state == "closed", "до начала окна в свой день приём закрыт")

        seconds = db.seconds_to_open(lst, monday_morning)
        check(abs(seconds - 10.5 * 3600) < 60,
              "обратный отсчёт до открытия считается верно")

        off = dict(lst); off["active"] = 0
        state, hint = db.list_window(conn, off, monday_evening)
        check(state == "closed" and "выключен" in hint,
              "выключенный лист не принимает пожелания")

        check(db.schedule_text(lst) == "по Пн с 20:00 до 21:00",
              "расписание одного дня описывается словами")
        every = dict(lst); every["weekdays"] = "1234567"
        check(db.schedule_text(every) == "ежедневно с 20:00 до 21:00",
              "ежедневное расписание описывается словами")

        check(db.list_reset_moment(lst, monday_evening.date()).strftime("%H:%M") == "19:55",
              "подготовка листа назначена за 5 минут до открытия")
    finally:
        db.set_setting(conn, "test_mode", "1")
        conn.close()


def test_vote_order_priority():
    print("\n[20] Приоритет по очереди голосования")
    participants = [(i, "Участник %d" % i) for i in range(1, 5)]
    prefs = {i: (1, 2, 3) for i in range(1, 5)}       # все хотят одно и то же
    order = {1: 0, 2: 1, 3: 2, 4: 3}                  # 1-й проголосовал раньше всех

    rows, log_lines, stats = assignment.solve(participants, prefs, 4, seed=1, order=order)
    place = {r["user_id"]: r["place"] for r in rows}
    check(stats["satisfied"] == 3 and stats["missed"] == 1,
          "трое получили желаемое, один — нет")
    check(place[4] == 4,
          "без желаемого места остался тот, кто проголосовал последним")
    check(place[1] == 1 and place[2] == 2 and place[3] == 3,
          "проголосовавшие раньше получили пожелания по старшинству: %s"
          % [place[i] for i in (1, 2, 3, 4)])

    # та же расстановка, но порядок голосования обратный
    order = {1: 3, 2: 2, 3: 1, 4: 0}
    rows, _, _ = assignment.solve(participants, prefs, 4, seed=1, order=order)
    place = {r["user_id"]: r["place"] for r in rows}
    check(place[1] == 4,
          "при обратном порядке компромисс достаётся другому — тому, кто позже")
    check(place[4] == 1, "первым проголосовавший получил первое пожелание")

    # приоритет не должен ломать основную цель: учтённых пожеланий столько же
    rows_a, _, stats_a = assignment.solve(participants, prefs, 4, seed=1,
                                          order={1: 0, 2: 1, 3: 2, 4: 3})
    rows_b, _, stats_b = assignment.solve(participants, prefs, 4, seed=1,
                                          order={1: 3, 2: 2, 3: 1, 4: 0})
    check(stats_a["satisfied"] == stats_b["satisfied"] == 3,
          "число учтённых пожеланий от очереди голосования не зависит")
    check(sorted(r["place"] for r in rows_a) == [1, 2, 3, 4] and
          sorted(r["place"] for r in rows_b) == [1, 2, 3, 4],
          "места остаются уникальными при любом порядке")

    # приоритет по рангам: двое хотят одно и то же, мест хватает обоим
    participants = [(1, "Ранний"), (2, "Поздний")]
    prefs = {1: (5, 6, 7), 2: (5, 6, 7)}
    rows, _, _ = assignment.solve(participants, prefs, 7, seed=1, order={1: 0, 2: 1})
    place = {r["user_id"]: r["place"] for r in rows}
    check(place[1] == 5 and place[2] == 6,
          "первое пожелание достаётся тому, кто проголосовал раньше")

    # сверка с полным перебором на том же наборе
    order = {1: 0, 2: 1, 3: 2, 4: 3}
    participants = [(i, "У%d" % i) for i in range(1, 5)]
    prefs = {1: (1, 2, 3), 2: (1, 2, 3), 3: (2, 3, 4), 4: (1, 2, 3)}
    rows, _, _ = assignment.solve(participants, prefs, 4, seed=2, order=order)
    got = {r["user_id"]: r["place"] for r in rows}
    best, _ = assignment.solve_bruteforce(participants, prefs, 4, order)
    check(got == best,
          "результат совпадает с полным перебором и по приоритету голосования")

    check(any("проголосовал(а)" in line for line in log_lines),
          "в журнале у каждого видно, каким по счёту он голосовал")
    check(any("приоритет у тех, кто проголосовал раньше" in line for line in log_lines),
          "правило приоритета записано в журнал расчёта")

def test_report_import():
    print("\n[21] Загрузка готового отчёта из CSV")
    lid = MAIN_LIST["id"]
    adm = client(); login(adm, "Пархачева Екатерина")
    day = web.today_str()

    csv_bytes = adm.get("/download/report/%d/%s.csv" % (lid, day)).data
    conn = db.connect()
    before = {r["full_name"]: (r["place"], r["status"])
              for r in db.get_results(conn, lid, day)}
    conn.close()
    check(len(before) == 26, "перед проверкой распределение есть")

    def upload(data, name="Очередь.csv", **extra):
        payload = {"file": (io.BytesIO(data), name)}
        payload.update(extra)
        return adm.post("/api/admin/import", data=payload,
                        content_type="multipart/form-data")

    def edit_rows(text, change):
        """Меняет строки таблицы в тексте отчёта и собирает файл обратно."""
        lines = text.replace("\r\n", "\n").split("\n")
        head = [i for i, l in enumerate(lines) if l.startswith("Место;")][0]
        change(lines, head)
        return "\n".join(lines).encode("utf-8-sig")

    # стираем распределение и восстанавливаем его из файла
    conn = db.connect()
    conn.execute("DELETE FROM results WHERE list_id = ? AND day = ?", (lid, day))
    conn.execute("DELETE FROM report_meta WHERE list_id = ? AND day = ?", (lid, day))
    conn.commit()
    check(len(db.get_results(conn, lid, day)) == 0, "распределение удалено")
    conn.close()

    r = upload(csv_bytes)
    check(r.status_code == 200, "файл принят")
    body = r.get_json()
    check(body["imported"] == 26, "загружены все 26 строк")
    check(body["day"] == day and body["list_id"] == lid,
          "лист и дата определены по содержимому файла")

    conn = db.connect()
    after = {r_["full_name"]: (r_["place"], r_["status"])
             for r_ in db.get_results(conn, lid, day)}
    conn.close()
    check(after == before, "распределение восстановлено один в один")

    cli = client(); login(cli, "Иванова Анна")
    card = user_list(cli, lid)
    check(card["my_result"] is not None and len(card["queue"]) == 26,
          "участники снова видят очередь и своё место")

    r = upload(csv_bytes)
    check(r.status_code == 200 and any("заменено" in w for w in r.get_json()["warnings"]),
          "повторная загрузка предупреждает о замене")

    # правка файла вручную: меняем двух человек местами
    text = csv_bytes.decode("utf-8-sig")
    holder = {}

    def swap_two(lines, head):
        first, second = lines[head + 1].split(";"), lines[head + 2].split(";")
        first[0], second[0] = second[0], first[0]
        holder["first"], holder["second"] = first, second
        lines[head + 1], lines[head + 2] = ";".join(first), ";".join(second)

    r = upload(edit_rows(text, swap_two), "Очередь_правленая.csv")
    check(r.status_code == 200, "правленный вручную файл принят")
    conn = db.connect()
    places = {r_["full_name"]: r_["place"] for r_ in db.get_results(conn, lid, day)}
    conn.close()
    full1 = " ".join(x for x in holder["first"][1:4] if x)
    full2 = " ".join(x for x in holder["second"][1:4] if x)
    check(places[full1] == int(holder["first"][0]) and
          places[full2] == int(holder["second"][0]),
          "перестановка мест из файла применилась")

    # файл в кодировке Excel
    r = upload(text.encode("cp1251"), "excel.csv")
    check(r.status_code == 200, "файл в кодировке Windows-1251 тоже читается")

    # ошибки
    r = upload("Просто текст без таблицы".encode("utf-8"), "мусор.csv")
    check(r.status_code == 400 and "Место" in r.get_json()["error"],
          "файл без таблицы отклонён с понятным сообщением")

    def duplicate_place(lines, head):
        second = lines[head + 2].split(";")
        second[0] = lines[head + 1].split(";")[0]
        lines[head + 2] = ";".join(second)

    r = upload(edit_rows(text, duplicate_place), "dup.csv")
    check(r.status_code == 400 and "занято несколько раз" in r.get_json()["error"],
          "повторяющиеся места отклонены")

    def stranger(lines, head):
        parts = lines[head + 1].split(";")
        parts[1], parts[2], parts[3] = "Пушкин", "Александр", "Сергеевич"
        lines[head + 1] = ";".join(parts)

    r = upload(edit_rows(text, stranger), "alien.csv")
    check(r.status_code == 400 and "нет в списке" in r.get_json()["error"],
          "незнакомые люди отклонены")

    def beyond_limit(lines, head):
        parts = lines[head + 1].split(";")
        parts[0] = "99"
        lines[head + 1] = ";".join(parts)

    r = upload(edit_rows(text, beyond_limit), "beyond.csv")
    check(r.status_code == 400 and "всего 26 мест" in r.get_json()["error"],
          "место за пределами листа отклонено")

    def not_a_number(lines, head):
        parts = lines[head + 1].split(";")
        parts[0] = "первое"
        lines[head + 1] = ";".join(parts)

    r = upload(edit_rows(text, not_a_number), "bad_place.csv")
    check(r.status_code == 400 and "должно быть числом" in r.get_json()["error"],
          "нечисловое место отклонено с указанием строки")

    unknown = text.replace("Лист;" + db.DEFAULT_LIST_NAME, "Лист;Несуществующий")
    r = upload(unknown.encode("utf-8-sig"), "unknown_list.csv")
    check(r.status_code == 400 and "выберите лист вручную" in r.get_json()["error"],
          "неизвестный лист требует ручного выбора")
    r = upload(unknown.encode("utf-8-sig"), "unknown_list.csv", list_id=str(lid))
    check(r.status_code == 200, "с явно выбранным листом такой файл загружается")

    plain = client(); login(plain, "Иванова Анна")
    r = plain.post("/api/admin/import",
                   data={"file": (io.BytesIO(csv_bytes), "x.csv")},
                   content_type="multipart/form-data")
    check(r.status_code == 403, "обычный пользователь загружать отчёты не может")

    conn = db.connect()
    events = db.read_events(conn, day, day)
    conn.close()
    check(any(e["action"] == "IMPORT" for e in events), "загрузка записана в журнал")
    check(any(e["action"] == "IMPORT_ERROR" for e in events),
          "неудачная попытка тоже записана в журнал")


def main():
    setup_site()
    for test in [test_algorithm_matches_bruteforce, test_all_wishes_satisfied,
                 test_conflict_and_nearest, test_silent_participants_random,
                 test_login_rules, test_window_closed, test_preferences_flow,
                 test_compute_and_report, test_queue_visible_to_users,
                 test_swap_flow, test_daily_reset, test_admin_password_change,
                 test_self_password_change, test_timezone_and_seed,
                 test_postgres_layer, test_admin_is_participant,
                 test_lists_crud, test_multiple_lists_same_day,
                 test_per_list_schedule, test_vote_order_priority,
                 test_report_import]:
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
