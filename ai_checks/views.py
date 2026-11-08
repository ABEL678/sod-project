"""UI ИИ-контура (Ф1): панель «Проверить», находки, подтверждение/отклонение.

Все вьюхи доступны только при AI_CHECKS_ENABLED=True (иначе 404) и только
пользователям с правом создавать замечания к документу
(storage.domain.services.check_issue_permission). Подтверждение находки
создаёт Issue через storage.domain.services.create_issue (сервис ядра
не изменяется).

Финальная архитектура ИИ-контура (этап 20):
- ИИ-поиск: фильтры по разделу/типу источника (треб. 8), история запросов
  с поиском/фильтрами/пагинацией (треб. 9, 10);
- База знаний: блоки «Документы проекта» и «Внутренние стандарты»
  (треб. 7), переиндексация проекта/документа (треб. 5);
- Отчёт ГИПу: история отчётов (треб. 13).
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date, timedelta

from PIL import Image, ImageDraw
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from ai_checks import pdfium_tools
from ai_checks.domain.models import (
    CheckRun,
    GeneratedReport,
    PendingCheckResult,
    RagQuery,
)
from ai_checks.domain.services import (
    KB_CODE_PREFIX,
    KNOWLEDGE_DOC_TYPES,
    bbox_ratios_for_rotation,
    crop_finding_image,
    generate_gip_report,
    gip_report_docx,
    gip_report_pdf,
    index_internal_document,
    knowledge_documents,
    rag_answer,
    reindex_document,
    reindex_project,
    remove_knowledge_document,
    upload_knowledge_document,
)
from storage.domain.services import get_accessible_projects
from ai_checks.tasks import (
    check_document_version_task,
    index_knowledge_document_task,
)
from storage.domain.models import Document, DocumentVersion
from storage.domain.services import check_issue_permission, create_issue

BBOX_MARGIN_PT = 12
PREVIEW_SCALE = 2  # был pymupdf.Matrix(2, 2)
BBOX_STROKE_PT = 1.2  # была толщина draw_rect в PyMuPDF

logger = logging.getLogger(__name__)

# Треб. 8: пример-подсказка (чип) для формы ИИ-поиска.
RAG_EXAMPLE_QUESTIONS = (
    'Какие нагрузки из ОВ учтены в ЭОМ?',
    'Какие требования заказчика к вентиляции?',
    'Что говорит внутренний стандарт о составе РЧ?',
)


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


@login_required
@require_GET
@never_cache  # HTMX-фрагмент: без этого браузер/SW может отдавать устаревший статус прогона
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
        # Поворот вьювера передаёт кнопка (hx-vals): bbox PyMuPDF —
        # неповёрнутая система, вьювер отображает с поворотом.
        ratios = bbox_ratios_for_rotation(
            version.file.path,
            finding.page,
            finding.bbox,
            request.POST.get('rotation'),
        )
        crop_base64 = None
        if finding.bbox and finding.page:
            try:
                crop_base64 = crop_finding_image(
                    version.file.path, finding.page, finding.bbox,
                )
            except (OSError, RuntimeError, ValueError):
                crop_base64 = None
        try:
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
                crop_image_base64=crop_base64,
            )
        except ValueError as exc:
            # Бэта-тест 2026-11-08: create_issue валидирует координаты и
            # кроп (<=5 МБ) и бросает ValueError — без перехвата это 500
            # «Подтвердить» (находка остаётся pending, можно повторить).
            logger.warning(
                'Подтверждение находки %s (версия %s): %s: %s',
                pk, version.pk, type(exc).__name__, exc,
            )
            response = _render_row(request, finding)
            response['HX-Trigger'] = 'ai-finding-error'
            return response
        finding.status = PendingCheckResult.Status.CONFIRMED
        finding.reviewed_by = request.user
        finding.reviewed_at = timezone.now()
        finding.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    response = _render_row(request, finding)
    # HTMX: обновить список/счётчики «Замечания» после создания Issue.
    response['HX-Trigger'] = 'ai-finding-confirmed'
    return response


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


def _preview_placeholder() -> Image.Image:
    """Серая заглушка вместо превью находки.

    Возвращается, когда PDF версии не читается (битый файл, OSError):
    500 здесь был бы виден как «сломанная картинка» в панели и в логах,
    а так панель остаётся работоспособной (маркап см. на листе).
    """
    image = Image.new('RGB', (320, 200), (238, 240, 243))
    ImageDraw.Draw(image).rectangle(
        [1, 1, 318, 198], outline=(180, 186, 196), width=1,
    )
    return image


def _render_finding_preview(version, finding: PendingCheckResult) -> Image.Image:
    """Кроп места находки с подсветкой bbox (как прежний код вьюхи)."""
    with pdfium_tools.open_pdf(version.file.path) as pdf:
        if finding.page is None or not (1 <= finding.page <= len(pdf)):
            view = pdfium_tools.PdfPageView(pdf, 0)
        else:
            view = pdfium_tools.PdfPageView(pdf, finding.page - 1)

        page_rect = view.rect
        bbox = finding.bbox or [
            page_rect.x0, page_rect.y0, page_rect.x1, page_rect.y1,
        ]
        clip = pdfium_tools.Rect(
            max(page_rect.x0, bbox[0] - BBOX_MARGIN_PT),
            max(page_rect.y0, bbox[1] - BBOX_MARGIN_PT),
            min(page_rect.x1, bbox[2] + BBOX_MARGIN_PT),
            min(page_rect.y1, bbox[3] + BBOX_MARGIN_PT),
        )
        if clip.is_empty or clip.width < 1 or clip.height < 1:
            clip = pdfium_tools.Rect(
                page_rect.x0, page_rect.y0,
                min(page_rect.x1, page_rect.x0 + 200),
                min(page_rect.y1, page_rect.y0 + 160),
            )
        try:
            image = view.render_clip(clip, scale=PREVIEW_SCALE)
        finally:
            view.close()

    draw = ImageDraw.Draw(image)
    box_px = [
        (bbox[0] - clip.x0) * PREVIEW_SCALE,
        (bbox[1] - clip.y0) * PREVIEW_SCALE,
        (bbox[2] - clip.x0) * PREVIEW_SCALE,
        (bbox[3] - clip.y0) * PREVIEW_SCALE,
    ]
    draw.rectangle(
        box_px,
        outline=(217, 26, 26),
        width=max(1, round(BBOX_STROKE_PT * PREVIEW_SCALE)),
    )
    return image


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
    try:
        image = _render_finding_preview(version, finding)
    except (pdfium_tools.PdfiumError, OSError, ValueError) as exc:
        # PdfiumError — битый/недочитанный PDF (Data format error и т.п.);
        # OSError — файл версии недоступен на диске. Раньше это было 500
        # и «сломанная» картинка в панели (см. /ai/finding/84/preview/).
        logger.warning(
            'Превью находки %s (версия %s): %s: %s',
            pk, version.pk, type(exc).__name__, exc,
        )
        image = _preview_placeholder()

    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return HttpResponse(
        buffer.getvalue(), content_type='image/png',
    )


def _rag_groups(sources: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """Группирует источники по типу (треб. 8): проект → стандарты."""
    order = ('project', 'standard')
    labels = {
        'project': 'Документы проекта',
        'standard': 'Внутренние стандарты',
    }
    groups = []
    for group in order:
        items = [
            source for source in sources
            if source.get('type', 'standard') == group
        ]
        if items:
            groups.append((group, labels[group], items))
    return groups


RAG_HISTORY_PAGE_SIZE = 20


@login_required
@require_GET
def rag_panel(request, project_id: int):
    """HTMX-фрагмент вкладки «ИИ-поиск»: форма вопроса и история."""
    _ensure_enabled()
    project = get_object_or_404(
        get_accessible_projects(request.user), pk=project_id,
    )
    return render(request, 'ai_checks/_rag.html', {
        'project': project,
        'sections': project.sections.order_by('order', 'pk'),
        'example_questions': RAG_EXAMPLE_QUESTIONS,
    })


@login_required
@require_POST
def rag_search(request, project_id: int):
    """HTMX-фрагмент: ответ и источники с цитатами (сгруппированы).

    Треб. 8: фильтры «раздел» (ОВ/ЭОМ/АР…) и «тип источника»
    (проект/стандарт). После ответа история запросов обновляется
    out-of-band (треб. 9).
    """
    _ensure_enabled()
    project = get_object_or_404(
        get_accessible_projects(request.user), pk=project_id,
    )
    question = (request.POST.get('question') or '').strip()
    section_code = (request.POST.get('section_code') or '').strip()
    source_type = (request.POST.get('source_type') or '').strip() or None
    error = ''
    result = {'answer': '', 'sources': []}
    if not question:
        error = 'Введите вопрос.'
    elif source_type not in (None, 'project', 'standard'):
        error = 'Некорректный тип источника.'
    else:
        try:
            result = rag_answer(
                question,
                project.pk,
                request.user,
                section_code=section_code or None,
                source_type=source_type,
                model=_selected_model(request),
            )
        except ValueError as exc:
            error = str(exc)
        except NotImplementedError:
            # SQLite-режим (dev без Docker): векторный поиск только на PG.
            error = (
                'Векторный поиск доступен только на PostgreSQL. '
                'Поднимите dev-стек: docker compose -f docker-compose.dev.yml up -d.'
            )

    response = render(request, 'ai_checks/_rag_results.html', {
        'project': project,
        'question': question,
        'answer': result['answer'],
        'sources': result['sources'],
        'groups': _rag_groups(result['sources']),
        'error': error,
    })
    if not error:
        # Треб. 9: история видна всем участникам — обновляем блок OOB.
        from django.template import loader

        history_html = loader.render_to_string(
            'ai_checks/_rag_history.html',
            {**_rag_history_context(project, request), 'oob': True},
            request=request,
        )
        response.content += history_html.encode(
            response.charset or 'utf-8',
        )
    return response


def _project_members(project) -> list:
    """Участники проекта (для фильтра истории по автору, треб. 10)."""
    members = {
        membership.user
        for membership in project.memberships.select_related('user')
        if membership.user_id
    }
    if project.gip_id:
        members.add(project.gip)
    return sorted(members, key=lambda user: (user.last_name, user.username))


def _rag_history_context(project, request) -> dict:
    """Контекст истории ИИ-поиска: фильтры, пагинация (треб. 9, 10)."""
    queries = RagQuery.objects.filter(project=project).select_related('user')

    search_q = (request.GET.get('q') or request.POST.get('q') or '').strip()
    if search_q:
        queries = queries.filter(question__icontains=search_q)

    author_id = (
        request.GET.get('author') or request.POST.get('author') or ''
    ).strip()
    if author_id.isdigit():
        queries = queries.filter(user_id=int(author_id))

    date_from_raw = (
        request.GET.get('date_from') or request.POST.get('date_from') or ''
    ).strip()
    date_to_raw = (
        request.GET.get('date_to') or request.POST.get('date_to') or ''
    ).strip()
    try:
        if date_from_raw:
            queries = queries.filter(
                created_at__date__gte=date.fromisoformat(date_from_raw),
            )
        if date_to_raw:
            queries = queries.filter(
                created_at__date__lte=date.fromisoformat(date_to_raw),
            )
    except ValueError:
        # Некорректная дата — фильтр игнорируется, страница не падает.
        pass

    try:
        page_number = max(1, int(request.GET.get('page') or 1))
    except ValueError:
        page_number = 1
    page = Paginator(queries, RAG_HISTORY_PAGE_SIZE).get_page(page_number)

    return {
        'project': project,
        'page_obj': page,
        'members': _project_members(project),
        'filters': {
            'q': search_q,
            'author': author_id,
            'date_from': date_from_raw,
            'date_to': date_to_raw,
        },
    }


@login_required
@require_GET
def rag_history(request, project_id: int):
    """HTMX-фрагмент: история ИИ-поиска проекта (треб. 9, 10).

    Поиск по вопросу, фильтры по автору и датам, пагинация.
    Видна всем участникам проекта.
    """
    _ensure_enabled()
    project = get_object_or_404(
        get_accessible_projects(request.user), pk=project_id,
    )
    return render(
        request, 'ai_checks/_rag_history.html',
        _rag_history_context(project, request),
    )


@login_required
@require_GET
def rag_history_item(request, project_id: int, pk: int):
    """HTMX-фрагмент: ответ и источники выбранного запроса (треб. 9)."""
    _ensure_enabled()
    project = get_object_or_404(
        get_accessible_projects(request.user), pk=project_id,
    )
    query = get_object_or_404(
        RagQuery.objects.filter(project=project).select_related('user'),
        pk=pk,
    )
    return render(request, 'ai_checks/_rag_history_detail.html', {
        'project': project,
        'query': query,
        'groups': _rag_groups(query.sources or []),
    })


def _selected_model(request) -> str | None:
    """Модель из UI: только из разрешённого списка настроек."""
    allowed = {
        code for code, _label in getattr(settings, 'AI_AVAILABLE_MODELS', [])
    }
    model = (request.POST.get('model') or '').strip()
    return model if model in allowed else None


def _report_allowed(user) -> bool:
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'userprofile', None)
    return profile is not None and profile.role in ('gip', 'admin')


def _report_project(request, project_id: int):
    project = get_object_or_404(
        get_accessible_projects(request.user), pk=project_id,
    )
    if not _report_allowed(request.user):
        raise Http404('Отчёт ГИПу доступен только ГИП/администратору')
    return project


@login_required
@require_GET
def report_panel(request, project_id: int):
    """HTMX-фрагмент вкладки «Отчёт ГИПу»: форма периода и история.

    Треб. 13: под формой — список последних отчётов (дата, период,
    автор, кнопки PDF/Word). Вкладка целиком видна только ГИП/админ.
    """
    _ensure_enabled()
    project = _report_project(request, project_id)
    today = timezone.localdate()
    return render(request, 'ai_checks/_report.html', {
        'project': project,
        'default_start': (today - timedelta(days=30)).isoformat(),
        'default_end': today.isoformat(),
        'reports': _gip_reports(project),
    })


def _gip_reports(project):
    """История отчётов ГИПа (треб. 13): последние 20 по дате."""
    return (
        GeneratedReport.objects
        .filter(project=project)
        .select_related('created_by')[:20]
    )


@login_required
@require_POST
def report_generate(request, project_id: int):
    """HTMX-фрагмент: сводка отчёта + ссылка на скачивание JSON."""
    _ensure_enabled()
    project = _report_project(request, project_id)
    error = ''
    report = None
    try:
        period_start = date.fromisoformat(
            (request.POST.get('period_start') or '').strip(),
        )
        period_end = date.fromisoformat(
            (request.POST.get('period_end') or '').strip(),
        )
    except ValueError:
        error = 'Укажите корректные даты периода.'
    else:
        try:
            report = generate_gip_report(
                project, period_start, period_end, request.user,
                model=_selected_model(request),
            )
        except ValueError as exc:
            error = str(exc)

    # UX-аудит P0-3: человекочитаемый период формируется во вьюхе.
    # Раньше шаблон выводил report.period (ISO 'YYYY-MM-DD..YYYY-MM-DD'
    # из модели) — без дат в привычном формате; ключа payload.period
    # в payload вообще не существует.
    return render(request, 'ai_checks/_report_result.html', {
        'project': project,
        'report': report,
        'error': error,
        'reports': _gip_reports(project),
        'period_label': (
            f'{period_start:%d.%m.%Y} — {period_end:%d.%m.%Y}'
            if report else ''
        ),
    })


def _report_for_download(request, pk: int):
    _ensure_enabled()
    report = get_object_or_404(
        GeneratedReport.objects.select_related('project'), pk=pk,
    )
    get_object_or_404(
        get_accessible_projects(request.user), pk=report.project_id,
    )
    if not _report_allowed(request.user):
        raise Http404('Отчёт ГИПу доступен только ГИП/администратору')
    return report


@login_required
@require_GET
def report_pdf(request, pk: int):
    """Скачивание отчёта ГИПа в PDF (PyMuPDF)."""
    report = _report_for_download(request, pk)
    response = HttpResponse(
        gip_report_pdf(report), content_type='application/pdf',
    )
    response['Content-Disposition'] = (
        f'attachment; filename="gip_report_{report.pk}.pdf"'
    )
    return response


@login_required
@require_GET
def report_docx(request, pk: int):
    """Скачивание отчёта ГИПа в Word (python-docx)."""
    report = _report_for_download(request, pk)
    response = HttpResponse(
        gip_report_docx(report),
        content_type=(
            'application/vnd.openxmlformats-officedocument'
            '.wordprocessingml.document'
        ),
    )
    response['Content-Disposition'] = (
        f'attachment; filename="gip_report_{report.pk}.docx"'
    )
    return response


# ---------- База знаний: UI проекта ----------

def _knowledge_allowed(user) -> bool:
    return _report_allowed(user)


def _kb_project(request, project_id: int):
    if not _knowledge_allowed(request.user):
        raise Http404('База знаний доступна только ГИП/администратору')
    return get_object_or_404(
        get_accessible_projects(request.user), pk=project_id,
    )


def _kb_document(request, project_id: int, pk: int):
    project = _kb_project(request, project_id)
    document = knowledge_documents(project).filter(pk=pk).first()
    if document is None:
        raise Http404('Документ базы знаний не найден')
    return project, document


def project_documents(project):
    """Документы проекта для блока «Документы проекта» (треб. 7).

    Текущие версии неудалённых документов (кроме KB-* — они показаны
    в блоке «Внутренние стандарты») с бейджем ИИ-индексации (треб. 3).
    """
    return (
        DocumentVersion.objects
        .filter(
            document__project=project,
            document__is_deleted=False,
            document__current_version=F('pk'),
        )
        .exclude(document__code__startswith=KB_CODE_PREFIX)
        .select_related('document', 'document__section', 'document__folder')
        .annotate(chunk_count=Count('ai_chunks'))
        .order_by('document__section__order', 'document__code')
    )


def _render_kb_projects(request, project):
    return render(request, 'ai_checks/_kb_projects.html', {
        'project': project,
        'project_versions': project_documents(project),
    })


def _render_kb_table(request, project):
    return render(request, 'ai_checks/_kb_table.html', {
        'project': project,
        'documents': knowledge_documents(project),
    })


@login_required
@require_GET
def kb_panel(request, project_id: int):
    _ensure_enabled()  # гейт флага (был пропущен — БЗ открывалась при выключенном ИИ-контуре)
    """Вкладка «База знаний» (треб. 7): два блока + модалка загрузки.

    «Документы проекта» — таблица с ИИ-статусом и кнопкой
    «Переиндексировать проект» (треб. 5); «Внутренние стандарты» —
    таблица InternalDocument с кнопкой «Загрузить стандарт».
    """
    project = _kb_project(request, project_id)
    return render(request, 'ai_checks/_kb.html', {
        'project': project,
        'documents': knowledge_documents(project),
        'doc_types': [
            ('customer_req', 'Требования заказчика'),
            ('internal_standard', 'Внутренний стандарт'),
            ('past_issues', 'Прошлые замечания'),
        ],
    })


@login_required
@require_GET
def kb_table(request, project_id: int):
    _ensure_enabled()  # гейт флага (был пропущен — БЗ открывалась при выключенном ИИ-контуре)
    """HTMX-фрагмент: таблица документов базы знаний (стандарты)."""
    project = _kb_project(request, project_id)
    return _render_kb_table(request, project)


@login_required
@require_GET
def kb_projects_table(request, project_id: int):
    _ensure_enabled()  # гейт флага (был пропущен — БЗ открывалась при выключенном ИИ-контуре)
    """HTMX-фрагмент: таблица «Документы проекта» (треб. 7)."""
    project = _kb_project(request, project_id)
    return _render_kb_projects(request, project)


@login_required
@require_POST
def kb_reindex_project_view(request, project_id: int):
    _ensure_enabled()  # гейт флага (был пропущен — БЗ открывалась при выключенном ИИ-контуре)
    """Кнопка «Переиндексировать проект» (треб. 5, 7)."""
    project = _kb_project(request, project_id)
    reindex_project(project.pk, request.user)
    return _render_kb_projects(request, project)


@login_required
@require_POST
def kb_reindex_document_view(request, project_id: int, document_id: int):
    _ensure_enabled()  # гейт флага (был пропущен — БЗ открывалась при выключенном ИИ-контуре)
    """Переиндексация одного документа проекта (треб. 5)."""
    project = _kb_project(request, project_id)
    get_object_or_404(
        Document.objects.filter(project=project, is_deleted=False),
        pk=document_id,
    )
    reindex_document(document_id, request.user)
    return _render_kb_projects(request, project)


@login_required
@require_POST
def kb_upload(request, project_id: int):
    _ensure_enabled()  # гейт флага (был пропущен — БЗ открывалась при выключенном ИИ-контуре)
    """Загрузка PDF в базу знаний + постановка индексации в Celery."""
    project = _kb_project(request, project_id)
    uploaded = request.FILES.get('file')
    if uploaded is None or not uploaded.name.lower().endswith('.pdf'):
        return _render_kb_table(request, project)
    doc_type = (request.POST.get('doc_type') or '').strip()
    tenant_scope = (request.POST.get('tenant_scope') or 'project').strip()
    title = (request.POST.get('title') or '').strip()[:255]
    try:
        internal = upload_knowledge_document(
            request.user, project, uploaded, title, doc_type, tenant_scope,
        )
    except ValueError:
        return _render_kb_table(request, project)
    index_knowledge_document_task.delay(internal.pk)
    return _render_kb_table(request, project)


@login_required
@require_POST
def kb_reindex(request, project_id: int, pk: int):
    _ensure_enabled()  # гейт флага (был пропущен — БЗ открывалась при выключенном ИИ-контуре)
    """Переиндексация документа базы знаний."""
    project, document = _kb_document(request, project_id, pk)
    document.indexing_status = 'pending'
    document.save(update_fields=['indexing_status'])
    index_knowledge_document_task.delay(document.pk)
    return _render_kb_table(request, project)


@login_required
@require_POST
def kb_disable(request, project_id: int, pk: int):
    _ensure_enabled()  # гейт флага (был пропущен — БЗ открывалась при выключенном ИИ-контуре)
    """Мягкое удаление из базы знаний (is_active=False)."""
    project, document = _kb_document(request, project_id, pk)
    remove_knowledge_document(document)
    return _render_kb_table(request, project)


@login_required
@require_GET
def kb_chunks(request, project_id: int, pk: int):
    _ensure_enabled()  # гейт флага (был пропущен — БЗ открывалась при выключенном ИИ-контуре)
    """Фрагмент: текст чанков документа базы знаний (первые 20)."""
    _project, document = _kb_document(request, project_id, pk)
    return render(request, 'ai_checks/_kb_chunks.html', {
        'document': document,
        'chunks': list(document.chunks.order_by('seq')[:20]),
    })