# -*- coding: utf-8 -*-
"""Расчёт порядка сдачи лабораторной по пожеланиям участников.

Задача: раздать каждому участнику ровно одно место из 1..N так, чтобы как можно
больше людей получили одно из трёх мест, которые они выбрали.

Это классическая задача о назначениях. Полный перебор всех перестановок для 26
человек невозможен (26! вариантов), поэтому применяется венгерский алгоритм
(метод Куна — Манкреса): он за O(n^3) даёт гарантированно оптимальное решение,
то есть в точности тот же ответ, что и перебор, только быстро. Для маленьких
наборов в тестах решение сверяется с настоящим перебором (solve_bruteforce).

Стоимость назначения подобрана лексикографически, приоритеты сверху вниз:
  1) как можно меньше людей, оставшихся вообще без выбранного места (MISS_W);
  2) как можно больше людей получают первое пожелание, затем второе (RANK_W);
  3) кому не хватило — поставить как можно ближе к желаемому месту (DIST_W).
"""

import random

MISS_W = 100000   # штраф: человек не получил ни одно из трёх пожеланий
RANK_W = 1000     # штраф за каждую ступень: 1-е -> 0, 2-е -> 1000, 3-е -> 2000
DIST_W = 1        # штраф за каждое место удаления от ближайшего пожелания

INF = float("inf")


# ----------------------------------------------------------- венгерский алгоритм

def hungarian(cost):
    """Минимальное по стоимости назначение строк на столбцы.

    cost — матрица n x m (n <= m). Возвращает список длины n: для каждой строки
    номер столбца (0-based).
    """
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    if n > m:
        raise ValueError("строк больше, чем столбцов: %d > %d" % (n, m))

    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)      # p[j] — строка, назначенная столбцу j (1-based)
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(0, m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    result = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            result[p[j] - 1] = j - 1
    return result


# --------------------------------------------------------------- стоимости

def cell_cost(place, wishes):
    """Штраф за то, что человек с пожеланиями wishes получил место place."""
    for rank, wish in enumerate(wishes):
        if place == wish:
            return RANK_W * rank
    nearest = min(abs(place - w) for w in wishes)
    return MISS_W + DIST_W * nearest


def build_cost_matrix(voter_wishes, places, rng=None):
    """Матрица штрафов «участник x место».

    Крошечный случайный джиттер (< 0.001) разводит одинаково хорошие варианты,
    чтобы при равных решениях люди не получали одни и те же места каждый день.
    Он заведомо меньше минимальной разницы настоящих стоимостей (она >= 1),
    поэтому оптимальность решения не нарушается.
    """
    rng = rng or random.Random()
    matrix = []
    for wishes in voter_wishes:
        row = [cell_cost(place, wishes) + rng.random() * 0.001
               for place in range(1, places + 1)]
        matrix.append(row)
    return matrix


def rank_of(place, wishes):
    """Номер пожелания (1..3), если место совпало, иначе None."""
    for idx, wish in enumerate(wishes):
        if place == wish:
            return idx + 1
    return None


# ------------------------------------------------------------- основной расчёт

def solve(participants, preferences, places, seed=None):
    """Распределяет участников по местам.

    participants — список (user_id, full_name) всех людей;
    preferences  — {user_id: (p1, p2, p3)} только для тех, кто проголосовал;
    places       — количество мест (>= числа участников);
    seed         — зерно генератора случайных чисел (для воспроизводимости).

    Возвращает (assignment, log_lines, stats), где assignment — список словарей
    по одному на участника.
    """
    rng = random.Random(seed)
    log_lines = []

    def note(text):
        log_lines.append(text)

    total = len(participants)
    if places < total:
        raise ValueError("мест (%d) меньше, чем участников (%d)" % (places, total))

    voters = [(uid, name) for uid, name in participants if uid in preferences]
    silent = [(uid, name) for uid, name in participants if uid not in preferences]

    note("Участников всего: %d, мест: %d" % (total, places))
    note("Прислали пожелания: %d" % len(voters))
    note("Не прислали пожелания (считаем, что место безразлично): %d" % len(silent))

    assignment = {}
    stats = {"total": total, "voters": len(voters), "silent": len(silent),
             "rank1": 0, "rank2": 0, "rank3": 0, "missed": 0}

    # 1. Оптимально расставляем тех, кто высказал пожелания.
    if voters:
        voter_wishes = [preferences[uid] for uid, _ in voters]
        matrix = build_cost_matrix(voter_wishes, places, rng)
        columns = hungarian(matrix)

        note("")
        note("--- Разбор пожеланий (венгерский алгоритм, точный оптимум) ---")
        for (uid, name), wishes, col in zip(voters, voter_wishes, columns):
            place = col + 1
            rank = rank_of(place, wishes)
            wish_text = ", ".join(str(w) for w in wishes)
            if rank:
                status = "satisfied"
                stats["rank%d" % rank] += 1
                note("УЧТЕНО      | %-32s | хотел(а): %-10s | получил(а) место %2d "
                     "(пожелание №%d)" % (name, wish_text, place, rank))
            else:
                status = "missed"
                stats["missed"] += 1
                nearest = min(wishes, key=lambda w: (abs(place - w), w))
                note("НЕ УЧТЕНО   | %-32s | хотел(а): %-10s | получил(а) место %2d "
                     "(ближайшее желаемое %d, отклонение %d)"
                     % (name, wish_text, place, nearest, abs(place - nearest)))
            assignment[uid] = {"user_id": uid, "full_name": name, "place": place,
                               "status": status, "rank": rank, "wishes": wish_text}

    # 2. Оставшиеся места случайным образом достаются тем, кто промолчал.
    taken = {row["place"] for row in assignment.values()}
    free = [p for p in range(1, places + 1) if p not in taken]
    rng.shuffle(free)

    if silent:
        note("")
        note("--- Случайное распределение оставшихся мест ---")
    for (uid, name), place in zip(silent, free):
        assignment[uid] = {"user_id": uid, "full_name": name, "place": place,
                           "status": "indifferent", "rank": None, "wishes": ""}
        note("БЕЗ ПОЖЕЛАНИЙ | %-32s | случайно получил(а) место %2d" % (name, place))

    satisfied = stats["rank1"] + stats["rank2"] + stats["rank3"]
    note("")
    note("--- Итог ---")
    note("Пожелания учтены полностью: %d из %d" % (satisfied, stats["voters"]))
    note("  первое пожелание: %d, второе: %d, третье: %d"
         % (stats["rank1"], stats["rank2"], stats["rank3"]))
    note("Не удалось учесть: %d (поставлены максимально близко к желаемому)"
         % stats["missed"])
    note("Распределены случайно (без пожеланий): %d" % stats["silent"])

    stats["satisfied"] = satisfied
    rows = sorted(assignment.values(), key=lambda r: r["place"])
    return rows, log_lines, stats


# --------------------------------------------------- перебор (для проверки)

def solve_bruteforce(participants, preferences, places):
    """Честный полный перебор. Используется только в тестах на малых наборах."""
    from itertools import permutations

    ids = [uid for uid, _ in participants]
    best, best_cost = None, INF
    for perm in permutations(range(1, places + 1), len(ids)):
        cost = 0
        for uid, place in zip(ids, perm):
            if uid in preferences:
                cost += cell_cost(place, preferences[uid])
        if cost < best_cost:
            best_cost, best = cost, perm
    return dict(zip(ids, best)), best_cost
