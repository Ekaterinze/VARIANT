# -*- coding: utf-8 -*-
"""Чтение выгруженного сайтом отчёта (CSV) обратно.

Нужно, чтобы восстановить готовое распределение: например, после перезапуска
хостинга с чистым диском или когда очередь поправили руками в Excel.
Разбор устроен по заголовкам колонок, а не по их порядку, поэтому файл можно
редактировать, переставлять строки и сохранять в кодировке Excel.
"""

import csv
import io

# Тексты статусов совпадают с теми, что пишутся в выгрузке (app.STATUS_TEXT).
STATUS_TEXT = {
    "satisfied": "пожелание учтено",
    "missed": "пожелание не учтено (ближайшее возможное место)",
    "indifferent": "не голосовал, место назначено случайно",
}
STATUS_BY_TEXT = {text: code for code, text in STATUS_TEXT.items()}

REQUIRED_COLUMNS = ("Место", "Фамилия", "Имя")
YES = ("да", "yes", "1", "+")


def decode_csv(raw):
    """Читает файл в UTF-8 или в кодировке, в которой его сохранил Excel."""
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("не удалось определить кодировку файла")


def parse_report_csv(text):
    """Разбирает отчёт в словарь с шапкой и строками распределения.

    Возвращает {list_name, day, created_at, summary, rows}, где каждая строка —
    {place, surname, name, patronymic, wishes, rank, status, swapped, line}.
    Если колонка «Результат» отсутствует или заполнена от руки, статус
    восстанавливается по пожеланиям и номеру сработавшего пожелания.
    """
    delimiter = ";" if text.count(";") >= text.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not rows:
        raise ValueError("файл пустой")

    header = {}
    start = None
    for idx, row in enumerate(rows):
        cells = [c.strip() for c in row]
        if cells and cells[0] == "Место":
            start = idx
            break
        if len(cells) >= 2 and cells[0]:
            header[cells[0]] = cells[1]
    if start is None:
        raise ValueError("в файле нет таблицы: не найдена строка с колонкой «Место»")

    columns = {name.strip(): pos for pos, name in enumerate(rows[start]) if name.strip()}
    for required in REQUIRED_COLUMNS:
        if required not in columns:
            raise ValueError("в таблице нет колонки «%s»" % required)

    def cell(row, name):
        pos = columns.get(name)
        value = row[pos].strip() if pos is not None and pos < len(row) else ""
        return "" if value == "-" else value

    parsed = []
    for line, row in enumerate(rows[start + 1:], start=start + 2):
        if not any(c.strip() for c in row):
            continue
        place = cell(row, "Место")
        if not place.isdigit():
            raise ValueError("строка %d: «%s» — место должно быть числом" % (line, place))
        surname, name = cell(row, "Фамилия"), cell(row, "Имя")
        if not surname or not name:
            raise ValueError("строка %d: не указаны фамилия и имя" % line)

        rank = cell(row, "Номер сработавшего пожелания")
        wishes = cell(row, "Пожелания")
        status = STATUS_BY_TEXT.get(cell(row, "Результат"))
        if status is None:                      # файл поправили вручную
            status = "satisfied" if rank.isdigit() else ("missed" if wishes
                                                         else "indifferent")
        parsed.append({
            "place": int(place),
            "surname": surname,
            "name": name,
            "patronymic": cell(row, "Отчество"),
            "wishes": wishes,
            "rank": int(rank) if rank.isdigit() else None,
            "status": status,
            "swapped": cell(row, "Был обмен").lower() in YES,
            "line": line,
        })
    if not parsed:
        raise ValueError("в таблице нет ни одной строки с людьми")

    return {
        "list_name": header.get("Лист", "").strip(),
        "day": header.get("Дата сдачи", "").strip(),
        "created_at": header.get("Дата формирования", "").strip(),
        "summary": header.get("Итог", "").strip(),
        "rows": parsed,
    }
