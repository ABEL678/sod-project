"""Celery-задачи ИИ-контура.

После зачистки нормоконтроля (2026-11-09) остались только задачи
индексации RAG: база знаний (InternalChunk) и документы проекта
(ProjectDocumentChunk). Задачи прогона правил (check_document_version)
и напоминаний по находкам (check_reminders) удалены вместе с моделями
CheckRun / PendingCheckResult / ReminderRule (миграция 0013).
"""

from celery import shared_task


@shared_task(ignore_result=True, name='ai_checks.index_knowledge_document')
def index_knowledge_document_task(internal_document_id: int) -> int:
    """Индексация документа базы знаний (статусы pending→processing→done/error)."""
    from ai_checks.domain.services import index_internal_document

    return index_internal_document(internal_document_id)


@shared_task(ignore_result=True, name='ai_checks.index_project_document')
def index_project_document_task(version_id: int) -> int:
    """Индексация PDF-версии документа проекта (треб. 4).

    Ставится автоматически из storage.domain.services.upload_document()
    при AI_CHECKS_ENABLED=True (или из reindex_project/reindex_document,
    треб. 5). Статус версии: pending → processing → done/error.
    """
    from ai_checks.domain.services import index_project_document

    return index_project_document(version_id)


@shared_task(ignore_result=True, name='ai_checks.reindex_project')
def reindex_project_task(project_id: int, user_id: int | None = None) -> int:
    """Резервная индексация всех документов проекта (треб. 5).

    Для больших проектов можно вызывать как задачу вместо постановки
    N отдельных задач: в текущем UI используется построчный вариант
    reindex_project() (одна задача на версию — виден прогресс по
    бейджам статуса).
    """
    from ai_checks.domain.services import reindex_project

    user = None
    if user_id is not None:
        from django.contrib.auth.models import User

        user = User.objects.filter(pk=user_id).first()
    return reindex_project(project_id, user)
