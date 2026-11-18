"""Тесты чистого ядра «Матрицы заданий» (`storage.domain.tasks_matrix`).

Только `SimpleTestCase` — модуль не касается БД. Кейсы — §6.1 плана
`docs/MATRIX_TASKS_PLAN.md` (К1 + Ф1–Ф3).
"""

from django.test import SimpleTestCase

from storage.domain.tasks_matrix import (
    CELL_CANCELLED_STATUSES,
    CELL_DONE_STATUSES,
    CELL_IN_PROGRESS_STATUSES,
    CELL_OVERDUE_STATUSES,
    CELL_REVIEW_STATUSES,
    CELL_REWORK_STATUSES,
    CELL_STATUS_DONE,
    CELL_STATUS_IN_PROGRESS,
    CELL_STATUS_NOT_STARTED,
    CELL_STATUS_OVERDUE,
    CELL_STATUS_REVIEW,
    CELL_STATUS_REWORK,
    CELL_STATUS_WAITING_INFO,
    CELL_STATUSES,
    CELL_WAITING_INFO_STATUSES,
    compute_cell_status,
)


def counters(non_cancelled=0, done=0, overdue=0, returned=0, on_hold=0,
             review=0, in_progress=0):
    """Счётчики пары разделов (все ключи явно)."""
    return {
        'non_cancelled': non_cancelled,
        'done': done,
        'overdue': overdue,
        'returned': returned,
        'on_hold': on_hold,
        'review': review,
        'in_progress': in_progress,
    }


class ComputeCellStatusTests(SimpleTestCase):
    # ─── Правило 1 → DONE ────────────────────────────────────────────────────

    def test_rule1_all_done(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, done=2)),
            CELL_STATUS_DONE,
        )

    def test_rule1_single_completed_task(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=1, done=1)),
            CELL_STATUS_DONE,
        )

    # ─── Правило 2 → OVERDUE ─────────────────────────────────────────────────

    def test_rule2_draft_with_expired_due_date(self):
        # Факт правила К1-2: DRAFT с просроченным сроком — ячейка OVERDUE.
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=1, overdue=1)),
            CELL_STATUS_OVERDUE,
        )

    def test_rule2_overdue_not_suppressed_by_done_partial(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, done=1, overdue=1)),
            CELL_STATUS_OVERDUE,
        )

    def test_overdue_counter_zero_with_review_gives_review(self):
        # Просрочка гасится статусами {COMPLETED, ACCEPTED, REVIEW, CANCELLED}:
        # такой предикат на ORM-уровне даёт overdue == 0, и ячейка идёт дальше
        # по цепочке — до правила REVIEW.
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, done=1, review=1)),
            CELL_STATUS_REVIEW,
        )

    def test_non_overdue_draft_is_not_started(self):
        # Строгое `<` (правило К1-2): задача со сроком «сегодня» даёт
        # overdue == 0 на ORM-уровне, ячейка остаётся NOT_STARTED. Сам
        # граничный предикат проверяется в M2 (§6.2, today-параметр);
        # здесь фиксируется поведение цепочки при overdue == 0.
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=1)),
            CELL_STATUS_NOT_STARTED,
        )

    # ─── Правило 3 → REWORK ──────────────────────────────────────────────────

    def test_rule3_returned(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, returned=1)),
            CELL_STATUS_REWORK,
        )

    # ─── Правило 4 → WAITING_INFO ────────────────────────────────────────────

    def test_rule4_on_hold(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, on_hold=1)),
            CELL_STATUS_WAITING_INFO,
        )

    # ─── Правило 5 → REVIEW ──────────────────────────────────────────────────

    def test_rule5_review(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, review=1)),
            CELL_STATUS_REVIEW,
        )

    # ─── Правило 6 → IN_PROGRESS ─────────────────────────────────────────────

    def test_rule6_in_progress(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, in_progress=1)),
            CELL_STATUS_IN_PROGRESS,
        )

    # ─── Правило 7 → NOT_STARTED (fallback) ──────────────────────────────────

    def test_rule7_draft_and_issued(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=3)),
            CELL_STATUS_NOT_STARTED,
        )

    # ─── Приоритеты К1 (первое совпадение решает) ────────────────────────────

    def test_priority_overdue_beats_rework(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, overdue=1, returned=1)),
            CELL_STATUS_OVERDUE,
        )

    def test_priority_rework_beats_waiting_info(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, returned=1, on_hold=1)),
            CELL_STATUS_REWORK,
        )

    def test_priority_waiting_info_beats_review(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, on_hold=1, review=1)),
            CELL_STATUS_WAITING_INFO,
        )

    def test_priority_review_beats_in_progress(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, review=1, in_progress=1)),
            CELL_STATUS_REVIEW,
        )

    def test_priority_done_beats_all(self):
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=2, done=2, overdue=1,
                                          returned=1, on_hold=1, review=1,
                                          in_progress=1)),
            CELL_STATUS_DONE,
        )

    # ─── Ф1: пара без живых задач — не DONE ──────────────────────────────────

    def test_f1_empty_pair_is_not_done(self):
        self.assertEqual(
            compute_cell_status(counters()),
            CELL_STATUS_NOT_STARTED,
        )

    def test_f1_all_cancelled_is_not_done(self):
        # Все задачи пары CANCELLED → non_cancelled == 0 → правило 1.
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=0)),
            CELL_STATUS_NOT_STARTED,
        )

    def test_f1_empty_pair_not_done_even_with_other_counters(self):
        # Вакуумная истинность «все не-CANCELLED в {}» исключена: правило 1
        # срабатывает раньше любых прочих счётчиков.
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=0, done=0, overdue=1,
                                          returned=1, review=1)),
            CELL_STATUS_NOT_STARTED,
        )

    # ─── Ф2: fallback тотален ────────────────────────────────────────────────

    def test_f2_draft_mix_cancelled_falls_to_fallback(self):
        # Смесь DRAFT+CANCELLED: non_cancelled=1, done=0, прочие признаки 0.
        self.assertEqual(
            compute_cell_status(counters(non_cancelled=1)),
            CELL_STATUS_NOT_STARTED,
        )

    # ─── Частичное завершение ────────────────────────────────────────────────

    def test_partial_completion_is_not_started(self):
        # COMPLETED + DRAFT без прочих признаков → NOT_STARTED, счётчики честные.
        data = counters(non_cancelled=2, done=1)

        self.assertEqual(compute_cell_status(data), CELL_STATUS_NOT_STARTED)
        self.assertEqual(data['done'], 1)
        self.assertEqual(data['non_cancelled'], 2)

    # ─── Граничные ───────────────────────────────────────────────────────────

    def test_all_counters_zero(self):
        self.assertEqual(
            compute_cell_status(counters()),
            CELL_STATUS_NOT_STARTED,
        )

    def test_missing_keys_treated_as_zero(self):
        self.assertEqual(compute_cell_status({}), CELL_STATUS_NOT_STARTED)
        self.assertEqual(compute_cell_status({'non_cancelled': 3}),
                         CELL_STATUS_NOT_STARTED)

    def test_result_always_in_seven_statuses(self):
        combos = [
            counters(),
            counters(non_cancelled=1),
            counters(non_cancelled=1, done=1),
            counters(non_cancelled=2, overdue=1),
            counters(non_cancelled=2, returned=1),
            counters(non_cancelled=2, on_hold=1),
            counters(non_cancelled=2, review=1),
            counters(non_cancelled=2, in_progress=1),
        ]

        for combo in combos:
            self.assertIn(compute_cell_status(combo), CELL_STATUSES)


class CellStatusConstantsTests(SimpleTestCase):
    """Составы статусов дословно совпадают с К1/Ф1–Ф3."""

    def test_done_statuses(self):
        self.assertEqual(CELL_DONE_STATUSES, frozenset({'COMPLETED', 'ACCEPTED'}))

    def test_overdue_statuses(self):
        self.assertEqual(
            CELL_OVERDUE_STATUSES,
            frozenset({'DRAFT', 'ISSUED', 'IN_PROGRESS', 'ON_HOLD', 'RETURNED'}),
        )

    def test_rework_statuses(self):
        self.assertEqual(CELL_REWORK_STATUSES, frozenset({'RETURNED'}))

    def test_waiting_info_statuses(self):
        self.assertEqual(CELL_WAITING_INFO_STATUSES, frozenset({'ON_HOLD'}))

    def test_review_statuses(self):
        self.assertEqual(CELL_REVIEW_STATUSES, frozenset({'REVIEW'}))

    def test_in_progress_statuses(self):
        self.assertEqual(CELL_IN_PROGRESS_STATUSES, frozenset({'IN_PROGRESS'}))

    def test_cancelled_statuses(self):
        self.assertEqual(CELL_CANCELLED_STATUSES, frozenset({'CANCELLED'}))

    def test_overdue_set_excludes_suppressing_statuses(self):
        # Просрочка гасится {COMPLETED, ACCEPTED, REVIEW, CANCELLED}.
        self.assertFalse(CELL_OVERDUE_STATUSES & CELL_DONE_STATUSES)
        self.assertFalse(CELL_OVERDUE_STATUSES & CELL_REVIEW_STATUSES)
        self.assertFalse(CELL_OVERDUE_STATUSES & CELL_CANCELLED_STATUSES)

    def test_seven_cell_statuses(self):
        self.assertEqual(len(CELL_STATUSES), 7)
        self.assertEqual(len(set(CELL_STATUSES)), 7)
