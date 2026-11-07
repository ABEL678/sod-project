"""UI ИИ-контура (Ф1): панель «Проверить», находки, подтверждение/отклонение.

Все вьюхи доступны только при AI_CHECKS_ENABLED=True (иначе 404) и только
пользователям с правом создавать замечания к документу
(storage.domain.services.check_issue_permission). Подтверждение находки
создаёт Issue через storage.domain.services.create_issue (сервис ядра
не изменяется).
"""

from __future__ import annotations

import pymupdf
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ai_checks.domain.models import CheckRun, PendingCheckResult
from ai_checks.domain.services import rag_answer
from storage.domain.services import get_accessible_projects
from ai_checks.tasks import check_document_version_task
from storage.domain.models import Document
from storage.domain.services import check_issue_permission, create_issue

BBOX_MARGIN_PT = 12


def _ensure_enabled() -> None:
    if not settings.AI_CHECKS_ENABLED:
        raise Http404('ИИ-контур выключен (AI_CHECKS_ENABLED=False)')


def _document_for(user, document_id: int) -> Document:
    document = get_object_or_404(Document, pk=document_id, is_deleted=False)
    if not check_issue_permission(user, document):
        raise Http404('Нет доступа к документу')
    return document


def _latest_run(document: Document):
    version = document.current_version
    if version is None:
        return None, None
    run = (
        CheckRun.objects
        .filter(document_version=version)
        .order_by('-created_at')
        .first()
    )
    return version, run


def _render_panel(request, document, run=None, error=''):
    version = document.current_version
    findings = []
    if run is not None:
        findings = list(run.results.select_related('rule').all())
    return render(request, 'ai_checks/panel.html', {
        'document': document,
        'version': version,
        'run': run,
        'findings': findings,
        'error': error,
    })


def _render_row(request, finding: PendingCheckResult):
    return render(request, 'ai_checks/_finding_row.html', {
        'finding': finding,
    })


def _bbox_ratios(version, finding: PendingCheckResult) -> dict:
    """Переводит bbox находки (пункты PDF) в ratios 0..1 для create_issue."""
    if finding.bbox is None or finding.page is None:
        return {}

    with pymupdf.open(version.file.path) as pdf:
        if finding.page < 1 or finding.page > pdf.page_count:
            return {}
        page_rect = pdf[finding.page - 1].rect

    width = max(float(page_rect.width), 1.0)
    height = max(float(page_rect.height), 1.0)
    x0, y0, x1, y1 = (float(value) for value in finding.bbox)

    def ratio(value, size):
        return min(1.0, max(0.0, value / size))

    x1_ratio = ratio(min(x0, x1), width)
    y1_ratio = ratio(min(y0, y1), height)
    x2_ratio = ratio(max(x0, x1), width)
    y2_ratio = ratio(max(y0, y1), height)
    if x1_ratio >= x2_ratio or y1_ratio >= y2_ratio:
        return {}
    return {
        'x1_ratio': x1_ratio,
        'y1_ratio': y1_ratio,
        'x2_ratio': x2_ratio,
        'y2_ratio': y2_ratio,
        'x_ratio': (x1_ratio + x2_ratio) / 2.0,
        'y_ratio': (y1_ratio + y2_ratio) / 2.0,
    }


@login_required
@require_GET
def panel(request, document_id: int):
    """HTMX-фрагмент: статус прогона и список находок."""
    _ensure_enabled()
    document = _document_for(request.user, document_id)
    version, run = _latest_run(document)
    return _render_panel(request, document, run=run)


@login_required
@require_POST
def start_check(request, document_id: int):
    """Запуск прогона (Celery; в dev — синхронно через eager-режим)."""
    _ensure_enabled()
    document = _document_for(request.user, document_id)
    version = document.current_version
    if version is None:
        return _render_panel(
            request, document, error='У документа нет текущей версии.',
        )
    check_document_version_task.delay(version.pk, request.user.pk)
    version, run = _latest_run(document)
    return _render_panel(request, document, run=run)


@login_required
@require_POST
def confirm_finding(request, pk: int):
    """Подтверждение находки: создаёт Issue через сервис ядра."""
    _ensure_enabled()
    finding = get_object_or_404(
        PendingCheckResult.objects.select_related(
            'check_run__document_version__document', 'rule',
        ),
        pk=pk,
    )
    document = finding.check_run.document_version.document
    _document_for(request.user, document.pk)

    if finding.status == PendingCheckResult.Status.PENDING:
        version = finding.check_run.document_version
        ratios = _bbox_ratios(version, finding)
        create_issue(
            request.user,
            document,
            description=f'[ИИ] {finding.message}',
            version=version,
            page_number=finding.page,
            x_ratio=ratios.get('x_ratio', 0.5 if finding.page else None),
            y_ratio=ratios.get('y_ratio', 0.5 if finding.page else None),
            x1_ratio=ratios.get('x1_ratio'),
            y1_ratio=ratios.get('y1_ratio'),
            x2_ratio=ratios.get('x2_ratio'),
            y2_ratio=ratios.get('y2_ratio'),
        )
        finding.status = PendingCheckResult.Status.CONFIRMED
        finding.reviewed_by = request.user
        finding.reviewed_at = timezone.now()
        finding.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    return _render_row(request, finding)


@login_required
@require_POST
def reject_finding(request, pk: int):
    """Отклонение находки: status=rejected."""
    _ensure_enabled()
    finding = get_object_or_404(
        PendingCheckResult.objects.select_related(
            'check_run__document_version__document',
        ),
        pk=pk,
    )
    document = finding.check_run.document_version.document
    _document_for(request.user, document.pk)

    if finding.status == PendingCheckResult.Status.PENDING:
        finding.status = PendingCheckResult.Status.REJECTED
        finding.reviewed_by = request.user
        finding.reviewed_at = timezone.now()
        finding.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    return _render_row(request, finding)


@login_required
@require_GET
def finding_preview(request, pk: int):
    """PNG-кроп места находки с подсветкой bbox (подсветка на PDF)."""
    _ensure_enabled()
    finding = get_object_or_404(
        PendingCheckResult.objects.select_related(
            'check_run__document_version__document',
        ),
        pk=pk,
    )
    document = finding.check_run.document_version.document
    _document_for(request.user, document.pk)

    version = finding.check_run.document_version
    with pymupdf.open(version.file.path) as pdf:
        if finding.page is None or not (1 <= finding.page <= pdf.page_count):
            page = pdf[0]
        else:
            page = pdf[finding.page - 1]

        page_rect = page.rect
        bbox = finding.bbox or [
            page_rect.x0, page_rect.y0, page_rect.x1, page_rect.y1,
        ]
        clip = pymupdf.Rect(
            max(page_rect.x0, bbox[0] - BBOX_MARGIN_PT),
            max(page_rect.y0, bbox[1] - BBOX_MARGIN_PT),
            min(page_rect.x1, bbox[2] + BBOX_MARGIN_PT),
            min(page_rect.y1, bbox[3] + BBOX_MARGIN_PT),
        )
        if clip.is_empty or clip.width < 1 or clip.height < 1:
            clip = pymupdf.Rect(
                page_rect.x0, page_rect.y0,
                min(page_rect.x1, page_rect.x0 + 200),
                min(page_rect.y1, page_rect.y0 + 160),
            )
        # Подсветка: рисуем рамку в памяти документа (файл не сохраняется).
        page.draw_rect(
            pymupdf.Rect(bbox), color=(0.85, 0.10, 0.10), width=1.2,
        )
        pixmap = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(2, 2))

    return HttpResponse(pixmap.tobytes('png'), content_type='image/png')


def _rag_groups(sources: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """Группирует источники по tenant_scope: проект → компания → общие."""
    order = ('project', 'company', 'global')
    labels = {
        'project': 'Проект',
        'company': 'Компания',
        'global': 'Общие знания',
    }
    groups = []
    for scope in order:
        items = [source for source in sources if source['tenant_scope'] == scope]
        if items:
            groups.append((scope, labels[scope], items))
    return groups


@login_required
@require_GET
def rag_panel(request, project_id: int):
    """HTMX-фрагмент вкладки «ИИ-поиск»: форма вопроса."""
    _ensure_enabled()
    project = get_object_or_404(
        get_accessible_projects(request.user), pk=project_id,
    )
    return render(request, 'ai_checks/_rag.html', {'project': project})


@login_required
@require_POST
def rag_search(request, project_id: int):
    """HTMX-фрагмент: ответ и источники с цитатами (сгруппированы)."""
    _ensure_enabled()
    project = get_object_or_404(
        get_accessible_projects(request.user), pk=project_id,
    )
    question = (request.POST.get('question') or '').strip()
    error = ''
    result = {'answer': '', 'sources': []}
    if not question:
        error = 'Введите вопрос.'
    else:
        try:
            result = rag_answer(
                question,
                company_id=project.company_id,
                project_id=project.pk,
            )
        except ValueError as exc:
            error = str(exc)
        except NotImplementedError:
            # SQLite-режим (dev без Docker): векторный поиск только на PG.
            error = (
                'Векторный поиск доступен только на PostgreSQL. '
                'Поднимите dev-стек: docker compose -f docker-compose.dev.yml up -d.'
            )

    return render(request, 'ai_checks/_rag_results.html', {
        'project': project,
        'question': question,
        'answer': result['answer'],
        'sources': result['sources'],
        'groups': _rag_groups(result['sources']),
        'error': error,
    })
