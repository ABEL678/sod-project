"""Статусы ячеек «Матрицы заданий»: чистое ядро агрегации.

Ноль I/O, ноль ORM-запросов. Вход — счётчики пары разделов, посчитанные
ORM-агрегацией (`tasks_matrix_for_project`), выход — один из 7 статусов
ячейки. Детерминизм: одинаковые счётчики → одинаковый статус. Цепочка
тотальна: любое сочетание счётчиков даёт ровно один статус.
"""

# ─── Составы статусов DesignTask (К1 + Ф1–Ф3, дословно) ──────────────────────
#
# Whitelist-семантика (риск Р6): статус DesignTask, которого нет ни в одном
# составе ниже, не срабатывает ни в одном правиле цепочки — такая задача
# попадает только в `total`/`non_cancelled` (фильтры ~CANCELLED) и на статус
# ячейки не влияет. Будущий REQUESTED не сломает правила 1–6.

# Правило 1 (DONE) и счётчик «завершено» (Ф3).
CELL_DONE_STATUSES = frozenset({'COMPLETED', 'ACCEPTED'})

# Правило 2 (OVERDUE) и счётчик «просрочено» (Ф3): один и тот же предикат
# просрочки — `due_date < today` И статус в этом составе. Просрочка гасится
# статусами {COMPLETED, ACCEPTED, REVIEW, CANCELLED}: сдал на проверку — работа
# сделана.
CELL_OVERDUE_STATUSES = frozenset({'DRAFT', 'ISSUED', 'IN_PROGRESS', 'ON_HOLD', 'RETURNED'})

# Правило 3 (REWORK).
CELL_REWORK_STATUSES = frozenset({'RETURNED'})

# Правило 4 (WAITING_INFO): у нас ON_HOLD означает «жду ИД».
CELL_WAITING_INFO_STATUSES = frozenset({'ON_HOLD'})

# Правило 5 (REVIEW) — явный статус ячейки.
CELL_REVIEW_STATUSES = frozenset({'REVIEW'})

# Правило 6 (IN_PROGRESS) и счётчик «в работе» (Ф3).
CELL_IN_PROGRESS_STATUSES = frozenset({'IN_PROGRESS'})

# Ф1/Ф3: отменённые задачи вне универсума матрицы — не попадают ни в одно
# правило и ни в один счётчик (`non_cancelled` = все статусы, кроме этого).
CELL_CANCELLED_STATUSES = frozenset({'CANCELLED'})

# ─── Статусы ячейки (7 значений, К1) ─────────────────────────────────────────

CELL_STATUS_DONE = 'DONE'
CELL_STATUS_OVERDUE = 'OVERDUE'
CELL_STATUS_REWORK = 'REWORK'
CELL_STATUS_WAITING_INFO = 'WAITING_INFO'
CELL_STATUS_REVIEW = 'REVIEW'
CELL_STATUS_IN_PROGRESS = 'IN_PROGRESS'
CELL_STATUS_NOT_STARTED = 'NOT_STARTED'

CELL_STATUSES = (
    CELL_STATUS_DONE,
    CELL_STATUS_OVERDUE,
    CELL_STATUS_REWORK,
    CELL_STATUS_WAITING_INFO,
    CELL_STATUS_REVIEW,
    CELL_STATUS_IN_PROGRESS,
    CELL_STATUS_NOT_STARTED,
)

def compute_cell_status(counters):
    """Статус ячейки матрицы по счётчикам пары разделов.

    Цепочка §3.4 (приоритет К1, первое совпадение решает):

    1. `non_cancelled == 0` → NOT_STARTED (Ф1: «всё отменено»/пустая пара — не DONE);
    2. `done == non_cancelled` → DONE;
    3. `overdue >= 1` → OVERDUE;
    4. `returned >= 1` → REWORK;
    5. `on_hold >= 1` → WAITING_INFO;
    6. `review >= 1` → REVIEW;
    7. `in_progress >= 1` → IN_PROGRESS;
    8. иначе → NOT_STARTED (Ф2: тотальный fallback).

    Статус DesignTask вне составов CELL_* не влияет на ячейку: он попадает
    только в `total`/`non_cancelled` (фильтры ~CANCELLED, уровень ORM) и не
    срабатывает ни в одном правиле 1–6 (whitelist Р6, будущий REQUESTED).

    Отсутствующий ключ счётчика трактуется как 0.
    """
    non_cancelled = counters.get('non_cancelled', 0)
    done = counters.get('done', 0)
    overdue = counters.get('overdue', 0)
    returned = counters.get('returned', 0)
    on_hold = counters.get('on_hold', 0)
    review = counters.get('review', 0)
    in_progress = counters.get('in_progress', 0)

    if non_cancelled == 0:
        return CELL_STATUS_NOT_STARTED
    if done == non_cancelled:
        return CELL_STATUS_DONE
    if overdue >= 1:
        return CELL_STATUS_OVERDUE
    if returned >= 1:
        return CELL_STATUS_REWORK
    if on_hold >= 1:
        return CELL_STATUS_WAITING_INFO
    if review >= 1:
        return CELL_STATUS_REVIEW
    if in_progress >= 1:
        return CELL_STATUS_IN_PROGRESS
    return CELL_STATUS_NOT_STARTED
