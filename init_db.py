# -*- coding: utf-8 -*-
"""Первоначальная настройка: создаёт базу, заводит людей из Люди.txt и выдаёт пароли.

Запуск:  python init_db.py
Повторный запуск безопасен: уже существующие пользователи не трогаются,
пароли им не перевыпускаются. Чтобы начать с нуля, удалите data/lab.db.
"""

import os
import sys
from datetime import datetime

import db


def main():
    if not os.path.exists(db.PEOPLE_FILE):
        print("Не найден файл со списком людей: %s" % db.PEOPLE_FILE)
        return 1

    conn = db.connect()
    db.init_schema(conn)

    created = db.import_people(conn)
    total = len(db.all_users(conn))

    if created:
        lines = ["Пароли для входа на сайт (фамилия, имя и пароль)",
                 "Сформировано: %s" % db.local_now().strftime("%d.%m.%Y %H:%M:%S"),
                 "Пароль — 6 символов: английские буквы и цифры.",
                 ""]
        width = max(len(name) for name, _ in created)
        for full_name, password in created:
            lines.append("%-*s  %s" % (width, full_name, password))
        text = "\n".join(lines) + "\n"

        mode = "a" if os.path.exists(db.PASSWORDS_FILE) else "w"
        with open(db.PASSWORDS_FILE, mode, encoding="utf-8") as fh:
            if mode == "a":
                fh.write("\n")
            fh.write(text)

        db.log(conn, "СИСТЕМА", "INIT",
               "заведено пользователей: %d, пароли записаны в %s"
               % (len(created), os.path.basename(db.PASSWORDS_FILE)))
        print(text)
        print("Пароли сохранены в файл: %s" % db.PASSWORDS_FILE)
    else:
        print("Новых людей не найдено — все уже заведены.")

    admin = db.get_user_by_login(conn, db.ADMIN_FULL_NAME)
    print("")
    print("Всего пользователей: %d" % total)
    print("Мест в очереди: %d" % db.places_count(conn))
    print("Администратор: %s" % (admin["full_name"] if admin else "не найден!"))
    print("Приём пожеланий: с %s до %s, сброс пожеланий в %s."
          % (db.OPEN_FROM.strftime("%H:%M"), db.OPEN_TO.strftime("%H:%M"),
             db.RESET_AT.strftime("%H:%M")))
    print("")
    print("Запуск сайта:  python app.py   (адрес http://localhost:5000)")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
