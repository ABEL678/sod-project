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

# Ключи счётчиков, которые читает compute_cell_status. Реестр нужен сервису
# tasks_matrix_for_project, чтобы собрать dict ровно в этом составе (З2:
# ядро принимает dict, NamedTuple не используется).
COUNTER_KEYS = (
    'non_cancelled',
    'done',
    'overdue',
    'returned',
    'on_hold',
    'review',
    'in_progress',
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


# ─── Итоги решётки и автосводка (M6, решения Координатора 1 и 5) ─────────────

# Русские подписи статусов ячейки (план §4.3): единый словарь для
# ИИ-сводки и экспорта XLSX/PDF. Иконки/бейджи UI остаются во вьюхе.
CELL_STATUS_LABELS = {
    CELL_STATUS_DONE: 'Готово',
    CELL_STATUS_OVERDUE: 'Просрочено',
    CELL_STATUS_REWORK: 'Доработка',
    CELL_STATUS_WAITING_INFO: 'Пауза',
    CELL_STATUS_REVIEW: 'На проверке',
    CELL_STATUS_IN_PROGRESS: 'В работе',
    CELL_STATUS_NOT_STARTED: 'Не начато',
}

# Отображаемые счётчики ячейки (К3/Ф3) — состав итогов строк/столбцов.
MATRIX_COUNT_KEYS = ('total', 'in_progress', 'overdue', 'done')

# Пары в доработке или паузе — показатель rework_wait_pairs (M3.2, G).
REWORK_WAIT_CELL_STATUSES = frozenset({CELL_STATUS_REWORK, CELL_STATUS_WAITING_INFO})


def matrix_sum(cells):
    """Сумма четырёх отображаемых счётчиков по списку ячеек (M3, G).

    Чистая функция; вынесена из views.py (`_matrix_sum`) в M6 по решению
    Координатора 5 — числа M3 не изменяются (проверяет локальный регресс).
    """
    return {key: sum(cell[key] for cell in cells) for key in MATRIX_COUNT_KEYS}


def visible_cells(sections, cells):
    """Видимые ячейки решётки: фактические пары вне диагонали.

    Диагональ (source == target) отображается «—» (ЗM3-2) и не учитывается
    в итогах и сводке. Порядок повторяет обход решётки — по строкам, оси
    уже отсортированы сервисом (order, code).
    """
    visible = []
    for i, source in enumerate(sections):
        for j, target in enumerate(sections):
            if i == j:
                continue
            cell = cells.get((source.pk, target.pk))
            if cell is not None:
                visible.append(cell)
    return visible


def matrix_summary(sections, cells):
    """Пять показателей сводки по видимым ячейкам (M3, G).

    Возвращает dict с теми же ключами, что раньше собирала вьюха
    (`_get_matrix_context`): overdue_tasks, overdue_pairs,
    rework_wait_pairs, done_tasks, total_tasks. Вынесено в M6
    (решение Координатора 5).
    """
    visible = visible_cells(sections, cells)
    return {
        'overdue_tasks': sum(cell['overdue'] for cell in visible),
        'overdue_pairs': sum(1 for cell in visible if cell['overdue'] >= 1),
        'rework_wait_pairs': sum(
            1 for cell in visible
            if cell['status'] in REWORK_WAIT_CELL_STATUSES
        ),
        'done_tasks': sum(cell['done'] for cell in visible),
        'total_tasks': sum(cell['total'] for cell in visible),
    }


def build_ai_summary(matrix):
    """Плоский текст автосводки «Матрицы заданий» для ИИ-поиска.

    Вход — данные `tasks_matrix_for_project` (dict с ключами
    sections/cells/today); выход — детерминированный plain text, ноль I/O
    (M6, решение Координатора 1). Числа в тексте совпадают с
    `matrix_summary` и счётчиками ячеек — один и тот же универсум:
    видимые пары (без диагонали ЗM3-2), оси без COMMON.
    Построчные списки задач по парам не включаются (решение 4).
    Проект без заданий → пустая строка: контур ИИ ничего не встраивает.
    """
    sections = matrix.get('sections') or []
    cells = matrix.get('cells') or {}
    today = matrix.get('today')
    visible = visible_cells(sections, cells)
    summary = matrix_summary(sections, cells)
    if summary['total_tasks'] == 0:
        return ''

    sums = matrix_sum(visible)
    lines = []
    header = 'Матрица заданий'
    if today is not None:
        header += f' на {today.isoformat()}'
    lines.append(header + ' (сводка по парам разделов, без COMMON).')
    lines.append(
        f"Задач всего: {sums['total']}; "
        f"в работе: {sums['in_progress']}; "
        f"просрочено: {sums['overdue']}; "
        f"завершено: {sums['done']}."
    )
    lines.append(
        f"Пар с заданиями: {len(visible)}; "
        f"пар с просрочкой: {summary['overdue_pairs']}; "
        f"пар в доработке или паузе: {summary['rework_wait_pairs']}."
    )
    status_counts = {
        status: sum(1 for cell in visible if cell['status'] == status)
        for status in CELL_STATUSES
    }
    lines.append(
        'Статусы пар: '
        + ', '.join(
            f'{CELL_STATUS_LABELS[status]} {status_counts[status]}'
            for status in CELL_STATUSES
        ) + '.'
    )
    overdue_pairs = [cell for cell in visible if cell['overdue'] >= 1]
    if overdue_pairs:
        lines.append(
            'Пары с просроченными заданиями (код: просрочено из всего):'
        )
        by_id = {section.pk: section for section in sections}
        for cell in overdue_pairs:
            source = by_id[cell['source_section_id']]
            target = by_id[cell['target_section_id']]
            lines.append(
                f"- {source.code} ({source.name}) — "
                f"{target.code} ({target.name}): "
                f"просрочено {cell['overdue']} из {cell['total']}."
            )
    return '\n'.join(lines)
