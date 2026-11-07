"""Celery-задачи ИИ-контура (Этап 19, Ф1)."""

from celery import shared_task

from ai_checks.domain.services import run_rules_check


@shared_task(ignore_result=True, name='ai_checks.check_document_version')
def check_document_version_task(document_version_id: int, user_id: int) -> None:
    """Прогон детерминированных правил по версии документа.

    В dev/тестах (CELERY_TASK_ALWAYS_EAGER=True) выполняется синхронно;
    в проде — воркером Celery через Redis.
    """
    from django.contrib.auth.models import User

    from storage.domain.models import DocumentVersion

    try:
        version = DocumentVersion.objects.select_related('document').get(
            pk=document_version_id,
        )
        user = User.objects.get(pk=user_id)
    except (DocumentVersion.DoesNotExist, User.DoesNotExist):
        return

    run_rules_check(version, user)


@shared_task(ignore_result=True, name='ai_checks.check_reminders')
def check_reminders_task() -> int:
    """Ежедневные напоминания по незавершённым проверкам (Ф4, без LLM)."""
    from ai_checks.domain.services import send_pending_reminders

    return send_pending_reminders()
