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


@shared_task(name='storage.tasks.send_notification_email')
def send_notification_email(recipient_email: str, subject: str, message: str) -> None:
    """Отправляет email-уведомление получателю."""
    from django.core.mail import send_mail
    from django.conf import settings

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [recipient_email],
        fail_silently=True,
    )


# ─── S3 Data Lake (ГИП-Ассистент) — Этап B ───────────────────────────────────


@shared_task(name='storage.tasks.sync_project_to_s3')
def sync_project_to_s3(project_id: int) -> int:
    """Выгрузка одного проекта в S3."""
    from storage.domain.models import Project
    from storage.domain.sync.export import export_project_to_s3

    project = Project.objects.get(pk=project_id)
    return export_project_to_s3(project)


@shared_task(name='storage.tasks.sync_all_projects_to_s3')
def sync_all_projects_to_s3() -> int:
    """Выгрузка всех «грязных» проектов (плановый beat, раз в 15 минут)."""
    from storage.domain.models import SyncState
    from storage.domain.sync.export import export_project_to_s3

    count = 0
    for state in SyncState.objects.filter(dirty=True).select_related('project'):
        try:
            export_project_to_s3(state.project)
            count += 1
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception(
                "sync_all_projects_to_s3[%s]: %s", state.project.code, exc,
            )
    return count


@shared_task(name='storage.tasks.sync_s3_full')
def sync_s3_full() -> int:
    """Полная выгрузка всех проектов (раз в сутки)."""
    from storage.domain.models import Project, SyncState
    from storage.domain.sync.export import export_project_to_s3

    count = 0
    for project in Project.objects.filter(is_deleted=False):
        try:
            export_project_to_s3(project)
            count += 1
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception(
                "sync_s3_full[%s]: %s", project.code, exc,
            )
    if count:
        SyncState.objects.filter(project__is_deleted=False).update(
            last_full_sync_at=timezone.now(),
        )
    return count
