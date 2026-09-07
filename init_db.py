# -*- coding: utf-8 -*-
"""Первоначальная настройка: создаёт базу и заводит людей из Люди.txt.

Запуск:  python init_db.py
Стартовый пароль у всех одинаковый (a123456) — каждый участник меняет его сам
на странице «Мой пароль». Заодно создаётся лист голосования по умолчанию со
всеми людьми. Повторный запуск безопасен: уже заведённых людей скрипт не
трогает и пароли им не сбрасывает.
Чтобы начать с нуля, удалите data/lab.db и запустите скрипт снова.
"""

import os
import sys

import db


def main():
    if not os.path.exists(db.PEOPLE_FILE):
        print("Не найден файл со списком людей: %s" % db.PEOPLE_FILE)
        return 1

    conn = db.connect()
    db.init_schema(conn)

    created = db.import_people(conn)
    db.ensure_default_list(conn)
    total = len(db.all_users(conn))

    if created:
        db.log(conn, "СИСТЕМА", "INIT",
               "заведено пользователей: %d, стартовый пароль у всех одинаковый"
               % len(created))
        print("Заведено пользователей: %d" % len(created))
        for full_name, password in created:
            print("  %-32s %s" % (full_name, password))
    else:
        print("Новых людей не найдено — все уже заведены.")

    admin = db.get_user_by_login(conn, db.ADMIN_FULL_NAME)
    print("")
    print("Всего пользователей: %d" % total)
    print("Администратор: %s" % (admin["full_name"] if admin else "не найден!"))
    print("Стартовый пароль для всех: %s (меняется на сайте)" % db.DEFAULT_PASSWORD)
    print("Часовой пояс расписания: %s" % db.TZ_NAME)
    print("")
    print("Листы голосования:")
    for lst in db.all_lists(conn):
        print("  «%s» — %s, участников %d, мест %d"
              % (lst["name"], db.schedule_text(lst),
                 len(db.list_member_ids(conn, lst["id"])), db.list_places(conn, lst)))
    print("Новые листы создаются в панели администратора (вкладка «Листы»).")
    print("")
    print("Запуск сайта:  python app.py   (адрес http://localhost:5000)")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
