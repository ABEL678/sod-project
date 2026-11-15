"""Celery-задачи напоминаний по срокам заданий (Этап 5, спека).

Тонкие обёртки над сервисами уведомлений storage/domain/services.py:
выборка заданий, проверка получателей и идемпотентность живут в
сервисном слое («гексагон лайт»), здесь — только связка с Celery.
Расписание — CELERY_BEAT_SCHEDULE в sod_core/settings.py: due_* —
раз в сутки, overdue — раз в час.

Именованные задачи (name='storage.tasks.*') — стабильные имена для
записей beat_schedule и ручного запуска.
"""

from celery import shared_task

from storage.domain import services


@shared_task(name='storage.tasks.notify_task_due_soon')
def notify_task_due_soon() -> int:
    """За 2 дня до due_date (статусы ISSUED/IN_PROGRESS/ON_HOLD/REVIEW)."""
    return services.notify_tasks_due_soon()


@shared_task(name='storage.tasks.notify_task_due_today')
def notify_task_due_today() -> int:
    """В день due_date (те же открытые статусы)."""
    return services.notify_tasks_due_today()


@shared_task(name='storage.tasks.notify_task_overdue')
def notify_task_overdue() -> int:
    """На следующий день после due_date, если задание ещё не закрыто.

    Незакрытыми считаются все статусы, кроме COMPLETED/ACCEPTED/CANCELLED.
    """
    return services.notify_tasks_overdue()
