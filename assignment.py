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
  3) кому не хватило — поставить как можно ближе к желаемому месту (DIST_W);
  4) если вариантов с одинаковым результатом несколько — компромисс достаётся
     тем, кто проголосовал позже (см. «Очередь голосования» ниже).

Очередь голосования. Пожелания у людей часто пересекаются, и одинаково хороших
раскладов бывает много. Чтобы выбор между ними не был случайным, каждому
голосовавшему даётся вес: чем раньше человек отдал пожелания, тем вес больше.
Стоимость его неудобств умножается на этот вес, поэтому из равных вариантов
выбирается тот, где ближайшие (а не желаемые) места подобрали тем, кто
проголосовал позже. Вся арифметика целочисленная, поэтому такой порядок
предпочтений соблюдается точно, без ошибок округления.
"""

import random

MISS_W = 100000   # штраф: человек не получил ни одно из трёх пожеланий
RANK_W = 1000     # штраф за каждую ступень: 1-е -> 0, 2-е -> 1000, 3-е -> 2000
DIST_W = 1        # штраф за каждое место удаления от ближайшего пожелания

# Большое целое вместо бесконечности: вся арифметика в алгоритме целочисленная.
INF = 10 ** 30


# ----------------------------------------------------------- венгерский алгоритм

def hungarian(cost):
    """Минимальное по стоимости назначение строк на столбцы.

    cost — матрица n x m (n <= m) целых чисел. Возвращает список длины n:
    для каждой строки номер столбца (0-based).
    """
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    if n > m:
        raise ValueError("строк больше, чем столбцов: %d > %d" % (n, m))

    u = [0] * (n + 1)
    v = [0] * (m + 1)
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


def vote_weights(count, order=None):
    """Вес каждого голосовавшего: чем раньше проголосовал, тем больше.

    order — позиции в очереди голосования (0 — проголосовал первым). Если её нет,
    все веса одинаковые и приоритет по времени не применяется.
    """
    if not order:
        return [1] * count
    return [count - int(pos) for pos in order]


def tie_scale(count, places):
    """Множитель, отделяющий основную цель от приоритета по очереди голосования.

    Он строго больше любой возможной суммы «вес x штраф», поэтому разница даже
    в один балл основной стоимости всегда важнее любого приоритета.
    """
    worst = MISS_W + DIST_W * max(places, 1)
    return count * count * worst + 1


def build_cost_matrix(voter_wishes, places, weights=None):
    """Матрица штрафов «участник x место» с учётом очереди голосования.

    Итоговая стоимость = базовый штраф * (tie_scale + вес участника).
    Первое слагаемое решает задачу по существу, второе — разводит одинаково
    хорошие варианты в пользу тех, кто проголосовал раньше.
    """
    count = len(voter_wishes)
    weights = weights or [1] * count
    scale = tie_scale(count, places)
    matrix = []
    for wishes, weight in zip(voter_wishes, weights):
        matrix.append([cell_cost(place, wishes) * (scale + weight)
                       for place in range(1, places + 1)])
    return matrix


def rank_of(place, wishes):
    """Номер пожелания (1..3), если место совпало, иначе None."""
    for idx, wish in enumerate(wishes):
        if place == wish:
            return idx + 1
    return None


# ------------------------------------------------------------- основной расчёт

def solve(participants, preferences, places, seed=None, order=None):
    """Распределяет участников по местам.

    participants — список (user_id, full_name) всех людей;
    preferences  — {user_id: (p1, p2, p3)} только для тех, кто проголосовал;
    places       — количество мест (>= числа участников);
    seed         — зерно генератора случайных чисел (для воспроизводимости);
    order        — {user_id: номер в очереди голосования}, 0 — проголосовал
                   первым. Используется, когда несколько раскладов одинаково
                   хороши: компромисс достаётся тем, кто проголосовал позже.

    Возвращает (assignment, log_lines, stats).
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
        if order:
            positions = [order.get(uid, len(voters)) for uid, _ in voters]
        else:
            positions = None
        weights = vote_weights(len(voters), positions)
        matrix = build_cost_matrix(voter_wishes, places, weights)
        columns = hungarian(matrix)

        note("")
        note("--- Разбор пожеланий (венгерский алгоритм, точный оптимум) ---")
        if order:
            note("При равных вариантах приоритет у тех, кто проголосовал раньше:"
                 " ближайшие места вместо желаемых подбираются тем, кто позже.")
        for idx, ((uid, name), wishes, col) in enumerate(
                zip(voters, voter_wishes, columns)):
            place = col + 1
            rank = rank_of(place, wishes)
            wish_text = ", ".join(str(w) for w in wishes)
            queue = ("проголосовал(а) %d-м" % (positions[idx] + 1)) if positions else "-"
            if rank:
                status = "satisfied"
                stats["rank%d" % rank] += 1
                note("УЧТЕНО      | %-32s | хотел(а): %-10s | %-20s | получил(а) место %2d "
                     "(пожелание №%d)" % (name, wish_text, queue, place, rank))
            else:
                status = "missed"
                stats["missed"] += 1
                nearest = min(wishes, key=lambda w: (abs(place - w), w))
                note("НЕ УЧТЕНО   | %-32s | хотел(а): %-10s | %-20s | получил(а) место %2d "
                     "(ближайшее желаемое %d, отклонение %d)"
                     % (name, wish_text, queue, place, nearest, abs(place - nearest)))
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

def solve_bruteforce(participants, preferences, places, order=None):
    """Честный полный перебор. Используется только в тестах на малых наборах.

    Считает ту же стоимость, что и основной алгоритм, включая приоритет по
    очереди голосования, поэтому результаты можно сравнивать напрямую.
    """
    from itertools import permutations

    ids = [uid for uid, _ in participants]
    voters = [uid for uid in ids if uid in preferences]
    positions = [order.get(uid, len(voters)) for uid in voters] if order else None
    weights = dict(zip(voters, vote_weights(len(voters), positions)))
    scale = tie_scale(len(voters), places)

    best, best_cost = None, None
    for perm in permutations(range(1, places + 1), len(ids)):
        cost = 0
        for uid, place in zip(ids, perm):
            if uid in preferences:
                cost += cell_cost(place, preferences[uid]) * (scale + weights[uid])
        if best_cost is None or cost < best_cost:
            best_cost, best = cost, perm
    return dict(zip(ids, best)), best_cost
