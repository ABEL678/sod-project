import hashlib
import json
import re
import uuid
import zipfile
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseGone,
    HttpResponseNotFound,
    JsonResponse,
)
from django import forms
from django.contrib.staticfiles import finders
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.safestring import mark_safe
from django.utils.text import get_valid_filename
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView, UpdateView
from django_scotty.views import CottonTableView
from openpyxl import Workbook
from openpyxl.styles import Font

from storage.adapters.filters import DocumentFilter
from storage.adapters.local_storage import LocalFileStorage
from storage.adapters.tables import DocumentTable
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Feedback,
    Folder,
    MailRule,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    DOCUMENT_VIEW_COLUMNS,
    add_company_member,
    allowed_issue_statuses,
    archive_project,
    can_archive_project,
    can_create_project,
    can_delete_project,
    can_edit_project,
    can_manage_project_participants,
    can_restore_project,
    check_issue_permission,
    can_purge_trash,
    check_read_permission,
    check_write_permission,
    check_zone_permission,
    copy_document,
    create_folder,
    create_project,
    create_project_from_template,
    create_section,
    delete_document,
    delete_documents,
    delete_folder,
    delete_project,
    empty_trash,
    export_pdf_with_issues,
    generate_share_link,
    get_accessible_documents,
    get_accessible_projects,
    get_accessible_sections,
    get_deleted_projects,
    get_readable_documents,
    rename_section,
    reorder_folders,
    get_document_version_for_view,
    get_document_versions,
    get_document_view,
    get_flat_folders,
    get_folder_documents,
    get_folder_mark_id,
    get_folder_tree,
    get_issue_assignees,
    get_issues_for_version,
    get_project_documents_for_view,
    get_project_documents_queryset,
    get_project_membership,
    get_sections_with_counts,
    get_trash,
    get_company_members,
    get_user_company,
    get_zone_counts,
    revoke_share_link,
    document_actions,
    document_workflow_actions,
    _effective_zone,
    _open_issues_count,
    transition_document_zone,
    update_internal_status,
    ZONE_WORKFLOW_ACTIONS,
    list_document_views,
    list_feedback,
    list_project_participants,
    move_document,
    remove_company_member,
    move_document_to_section,
    move_documents,
    move_folder,
    normalize_document_view_config,
    restore_project,
    rename_folder,
    reset_project_structure,
    restore_document,
    restore_folder,
    save_document_view,
    search_documents,
    submit_feedback,
    update_company_info,
    update_feedback_status,
    update_project,
    update_project_member_role,
    upload_document,
)

from issues.models import Issue
from storage.domain.project_templates import ROOT_SUPERFOLDER_NAMES


RECENT_DOCUMENTS_SESSION_KEY = 'recent_documents'
RECENT_DOCUMENTS_TS_KEY = 'recent_documents_ts'
RECENT_DOCUMENTS_LIMIT = 10

DOCUMENT_VIEW_COLUMN_LABELS = {
    'code': 'Шифр',
    'section': 'Раздел',
    'folder': 'Папка',
    'name': 'Название',
    'doc_type': 'Тип',
    'suitability': 'Статус для передачи',
    'internal_status': 'Внутренний статус',
    'issues': 'Замечания',
    'version': 'Версия',
    'date': 'Дата',
    'author': 'Автор',
}

DEFAULT_COLUMN_WIDTHS = {
    'selection': 36,
    'code': 120,
    'section': 100,
    'folder': 150,
    'name': 315,
    'doc_type': 100,
    'suitability': 180,
    'internal_status': 170,
    'issues': 90,
    'version': 70,
    'date': 130,
    'author': 120,
}

DOCUMENT_TABLE_COLUMN_MAP = {
    'selection': 'selection',
    'code': 'code',
    'section': 'section',
    'folder': 'folder',
    'name': 'name',
    'doc_type': 'doc_type',
    'suitability_code': 'suitability',
    'internal_status': 'internal_status',
    'issues': 'issues',
    'current_version': 'version',
    'updated_at': 'date',
    'created_by': 'author',
}

ALWAYS_VISIBLE_TABLE_COLUMNS = {
    'selection', 'actions', 'suitability_code', 'internal_status', 'issues',
}

# Иконки зон ISO 19650 для сегмент-контрола (синхронно с tables.py)
ZONE_WORKFLOW_ICONS = {
    'WIP': 'bi-pencil-square',
    'SHARED': 'bi-people',
    'PUBLISHED': 'bi-shield-check',
    'ARCHIVE': 'bi-lock',
}

FILTER_QUERY_KEYS = (
    'section', 'doc_type', 'status', 'suitability_code', 'internal_status',
    'code', 'name',
)

# Спринт 8: чипы активных фильтров — (param, подпись, choices для display).
FILTER_CHIP_FIELDS = (
    ('q', 'Поиск', None),
    ('section', 'Раздел', None),
    ('doc_type', 'Тип', Document.DOC_TYPE_CHOICES),
    ('status', 'Статус', Document.STATUS_CHOICES),
    ('suitability_code', 'Пригодность', Document.SUITABILITY_CHOICES),
    ('internal_status', 'Внутр. статус', Document.INTERNAL_STATUS_CHOICES),
    ('code', 'Шифр', None),
    ('name', 'Название', None),
)

DOCUMENT_SORT_COLUMN_MAP = {
    'code': 'code',
    'name': 'name',
    'date': 'updated_at',
    'version': 'current_version',
}

HIGHLIGHT_ROW_SECONDS = 5


def _document_column_defs(config):
    visible = set(config['columns'])
    widths = config.get('widths') or {}
    return [
        {
            'key': key,
            'label': DOCUMENT_VIEW_COLUMN_LABELS[key],
            'visible': key in visible,
            'width': widths.get(key, DEFAULT_COLUMN_WIDTHS[key]),
        }
        for key in DOCUMENT_VIEW_COLUMNS
    ]


def _overlay_request_filters(request, base_config, folder=None):
    """Накладывает GET-параметры на сохранённый вид таблицы.

    GET-параметры ``section``, ``folder`` и фильтры в шапке таблицы
    применяются автоматически, без отдельной кнопки «Применить».
    """
    config = {
        'columns': base_config['columns'],
        'widths': base_config.get('widths', {}),
        'filters': dict(base_config['filters']),
        'sort': list(base_config['sort']),
    }
    requested = False

    for key in FILTER_QUERY_KEYS:
        if key not in request.GET:
            continue
        requested = True
        value = (request.GET.get(key) or '').strip()
        if value:
            config['filters'][key] = value
        else:
            config['filters'].pop(key, None)

    if 'sort' in request.GET:
        requested = True
        requested_sort = (request.GET.get('sort') or '').strip()
        if not requested_sort:
            config['sort'] = []
        elif requested_sort.lstrip('-') in DOCUMENT_SORT_COLUMN_MAP:
            config['sort'] = [requested_sort]

    if folder is not None:
        requested = True
        if folder.section_id:
            config['filters']['section'] = folder.section_id
        else:
            config['filters'].pop('section', None)

    if not requested:
        return base_config

    try:
        return normalize_document_view_config(config)
    except ValueError:
        # Некорректные GET-параметры не должны ронять страницу проекта.
        return base_config


def _folder_chain(folder):
    """Возвращает путь папки от корня до текущей."""
    chain = []
    current = folder
    seen = set()
    while current is not None and current.pk not in seen:
        chain.append(current)
        seen.add(current.pk)
        current = current.parent
    return list(reversed(chain))


def _folder_has_superfolder_ancestor(folder):
    """Папка находится внутри одной из 4 корневых суперпапок T68."""
    current = folder
    seen = set()
    while current is not None and current.pk not in seen:
        if (
            current.parent_id is None
            and current.section_id is None
            and current.name in ROOT_SUPERFOLDER_NAMES
        ):
            return True
        seen.add(current.pk)
        current = current.parent
    return False


def _project_breadcrumbs(project, section=None, folder=None):
    """Хлебные крошки страницы проекта: Проект > Раздел > Папка."""
    project_url = reverse('storage:project_detail', args=[project.pk])
    crumbs = [{
        'label': project.name,
        'url': project_url,
        'active': section is None and folder is None,
    }]

    if folder is not None:
        if folder.section_id and not _folder_has_superfolder_ancestor(folder):
            crumbs.append({
                'label': f'{folder.section.code} — {folder.section.name}',
                'url': f'{project_url}?section={folder.section_id}',
                'active': False,
            })
        chain = _folder_chain(folder)
        for index, node in enumerate(chain):
            crumbs.append({
                'label': node.name,
                'url': f'{project_url}?folder={node.pk}',
                'active': index == len(chain) - 1,
            })
    elif section is not None:
        crumbs.append({
            'label': f'{section.code} — {section.name}',
            'url': f'{project_url}?section={section.pk}',
            'active': True,
        })

    return crumbs


def _document_breadcrumbs(document):
    """Хлебные крошки документа: Проект > Раздел > Папка > Документ."""
    project_url = reverse('storage:project_detail', args=[document.project_id])
    crumbs = [{
        'label': document.project.name,
        'url': project_url,
        'active': False,
    }]
    section = document.section
    if not _folder_has_superfolder_ancestor(document.folder):
        crumbs.append({
            'label': f'{section.code} — {section.name}',
            'url': f'{project_url}?section={section.pk}',
            'active': False,
        })
    for node in _folder_chain(document.folder):
        crumbs.append({
            'label': node.name,
            'url': f'{project_url}?folder={node.pk}',
            'active': False,
        })
    crumbs.append({
        'label': document.code,
        'url': '',
        'active': True,
    })
    return crumbs


def _remember_document(request, document_id):
    """Сохраняет в сессии ID и время недавних документов.

    Спринт 3: рядом с основным списком (совместимый формат — только ID)
    хранится словарь времён открытия RECENT_DOCUMENTS_TS_KEY — чипы
    «Последних документов» показывают, когда документ открывали.
    """
    recent = [
        pk
        for pk in request.session.get(RECENT_DOCUMENTS_SESSION_KEY, [])
        if isinstance(pk, int) and pk != document_id
    ]
    kept = [document_id, *recent][:RECENT_DOCUMENTS_LIMIT]
    request.session[RECENT_DOCUMENTS_SESSION_KEY] = kept

    stamps = request.session.get(RECENT_DOCUMENTS_TS_KEY, {})
    if not isinstance(stamps, dict):
        stamps = {}
    # ключи приходят строками (сессия хранится в JSON) — нормализуем к int
    kept_set = set(kept)
    normalized = {}
    for key, ts in stamps.items():
        try:
            pk = int(key)
        except (TypeError, ValueError):
            continue
        if pk in kept_set:
            normalized[pk] = ts
    normalized[document_id] = timezone.now().isoformat()
    request.session[RECENT_DOCUMENTS_TS_KEY] = normalized


def _recent_documents(request, user, project):
    """Недавние документы, отфильтрованные по текущим правам.

    Спринт 3: возвращает список словарей
    ``{'doc': Document, 'opened_at': datetime | None}`` — шаблон рисует
    чипы с типом, версией и временем открытия. Для старых сессий без
    времён ``opened_at`` равен None (чип просто не показывает время).
    """
    ids = [
        pk
        for pk in request.session.get(RECENT_DOCUMENTS_SESSION_KEY, [])
        if isinstance(pk, int)
    ]
    if not ids:
        return []

    stamps = request.session.get(RECENT_DOCUMENTS_TS_KEY, {})
    if not isinstance(stamps, dict):
        stamps = {}

    documents = {
        document.pk: document
        for document in get_accessible_documents(user).filter(
            project=project,
            pk__in=ids,
        ).select_related(
            'section',
            'current_version',
            'current_version__uploaded_by',
        )
    }
    result = []
    for pk in ids:
        doc = documents.get(pk)
        if doc is None:
            continue
        opened_at = None
        # ключ в сессии — строка (JSON), в свежезаписанном dict — int
        raw = stamps.get(pk) or stamps.get(str(pk))
        if raw:
            opened_at = parse_datetime(str(raw))
        result.append({'doc': doc, 'opened_at': opened_at})
        if len(result) >= RECENT_DOCUMENTS_LIMIT:
            break
    return result


class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = 'storage/project_list.html'
    context_object_name = 'projects'
    ordering = ['code']

    def get_queryset(self):
        if self.request.GET.get('tab') == 'trash':
            return get_deleted_projects(self.request.user)
        return get_accessible_projects(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        active_tab = 'trash' if self.request.GET.get('tab') == 'trash' else 'active'
        context['active_tab'] = active_tab
        context['can_create_project'] = can_create_project(user)
        context['project_status_choices'] = Project.STATUS_CHOICES
        projects = list(context['projects'])
        for project in projects:
            project.can_edit = (
                active_tab == 'active' and can_edit_project(user, project)
            )
            project.can_archive = (
                active_tab == 'active' and can_archive_project(user, project)
            )
            project.can_delete = (
                active_tab == 'active' and can_delete_project(user, project)
            )
            project.can_restore = (
                active_tab == 'trash' and can_restore_project(user, project)
            )
        context['projects'] = projects
        if context['can_create_project']:
            company = get_user_company(user)
            candidates = get_user_model().objects.filter(
                Q(userprofile__role__in=('gip', 'admin')) | Q(is_superuser=True)
            )
            if company is not None:
                candidates = candidates.filter(
                    Q(userprofile__company=company) | Q(is_superuser=True)
                )
            context['gip_candidates'] = candidates.distinct().order_by(
                'last_name',
                'username',
            )
        return context


class ProjectDetailView(LoginRequiredMixin, CottonTableView):
    model = Document
    table_class = DocumentTable
    filterset_class = DocumentFilter
    paginate_by = 50
    title = 'Документы проекта'
    template_name = 'storage/project_detail.html'
    strict = False
    export_formats = ('xlsx',)
    export_name = 'documents'
    available_filter_buttons = []
    show_filter_line = False
    show_bulk_actions = False
    table_show_download_link = False
    table_empty_text = mark_safe(
        '<div class="doc-empty">'
        '<i class="bi bi-folder2-open doc-empty-icon" aria-hidden="true"></i>'
        '<div class="doc-empty-title">Документов пока нет</div>'
        '<div class="doc-empty-hint">'
        'Перетащите файл в таблицу или загрузите его кнопкой «Загрузить»'
        '</div>'
        '<label for="fileInput" class="btn btn-primary btn-sm">'
        '<i class="bi bi-plus-lg" aria-hidden="true"></i> '
        'Загрузить первый документ'
        '</label>'
        '</div>'
    )

    def _get_project(self):
        if not hasattr(self, '_project_cache'):
            self._project_cache = get_object_or_404(
                get_accessible_projects(self.request.user),
                pk=self.kwargs['pk'],
            )
        return self._project_cache

    def _get_active_zone(self):
        """Сегмент-контрол зон ISO 19650 (?zone=WIP/SHARED/PUBLISHED/ARCHIVE)."""
        if not hasattr(self, '_active_zone_cache'):
            zone = (self.request.GET.get('zone') or '').strip()
            if zone not in dict(Folder.ZONE_CHOICES):
                zone = ''
            self._active_zone_cache = zone
        return self._active_zone_cache

    def _get_selected_folder(self):
        if not hasattr(self, '_selected_folder_cache'):
            folder = None
            raw_folder = (self.request.GET.get('folder') or '').strip()
            if raw_folder:
                try:
                    folder_id = int(raw_folder)
                except (TypeError, ValueError):
                    folder_id = None
                if folder_id is not None:
                    folder = get_flat_folders(
                        self.request.user,
                        self._get_project(),
                    ).filter(pk=folder_id).select_related('section', 'parent').first()

            # Выбор раздела в панели фильтров отменяет фильтр папки,
            # если пользователь переключился на другой раздел.
            raw_section = (self.request.GET.get('section') or '').strip()
            if folder is not None and raw_section:
                try:
                    section_id = int(raw_section)
                except (TypeError, ValueError):
                    section_id = None
                if section_id is not None and section_id != folder.section_id:
                    folder = None
            self._selected_folder_cache = folder
        return self._selected_folder_cache

    def _get_selected_section(self):
        if not hasattr(self, '_selected_section_cache'):
            folder = self._get_selected_folder()
            if folder is not None and folder.section_id:
                self._selected_section_cache = folder.section
            else:
                section = None
                raw_section = (self.request.GET.get('section') or '').strip()
                if raw_section:
                    try:
                        section_id = int(raw_section)
                    except (TypeError, ValueError):
                        section_id = None
                    if section_id is not None:
                        section = get_accessible_sections(
                            self.request.user,
                            self._get_project(),
                        ).filter(pk=section_id).first()
                self._selected_section_cache = section
        return self._selected_section_cache

    def _get_view_state(self):
        if not hasattr(self, '_view_state_cache'):
            user = self.request.user
            project = self._get_project()
            document_views = list_document_views(user, project)
            selected_view = get_document_view(
                user,
                project,
                self.request.GET.get('view'),
            )

            if selected_view is not None:
                try:
                    base_config = normalize_document_view_config(selected_view.config)
                except ValueError:
                    # Повреждённый JSON в БД не должен ронять страницу проекта.
                    selected_view = None
                    base_config = normalize_document_view_config({})
            else:
                base_config = normalize_document_view_config({})

            view_config = _overlay_request_filters(
                self.request,
                base_config,
                folder=self._get_selected_folder(),
            )

            self._view_state_cache = (document_views, selected_view, view_config)
        return self._view_state_cache

    def get_queryset(self):
        return get_project_documents_queryset(
            self.request.user,
            self._get_project(),
            self._get_view_state()[2],
            folder=self._get_selected_folder(),
            zone=self._get_active_zone() or None,
        )

    def get_table_data(self):
        if hasattr(self, 'object_list'):
            return self.object_list
        filterset = self.get_filterset(self.filterset_class)
        if filterset.is_valid():
            return filterset.qs
        return self.get_queryset()

    def get(self, request, *args, **kwargs):
        # Проверяем доступ к проекту до PaginationFixMixin, иначе его
        # обработчик Http404 превращает 404 в редирект.
        self._get_project()
        return super().get(request, *args, **kwargs)

    def get_filterset(self, filterset_class):
        filterset = super().get_filterset(filterset_class)
        section_field = filterset.form.fields.get('section')
        if section_field is not None:
            section_field.queryset = Section.objects.filter(
                project=self._get_project()
            ).exclude(code='COMMON')
        return filterset

    def get_filterset_kwargs(self, filterset_class):
        kwargs = super().get_filterset_kwargs(filterset_class)
        config = self._get_view_state()[2]
        data = self.request.GET.copy()
        changed = False
        for key in FILTER_QUERY_KEYS:
            value = config['filters'].get(key)
            if value and not data.get(key):
                data[key] = str(value)
                changed = True
        if changed:
            kwargs['data'] = data
        # Спринт 8: таблица должна строиться на аннотированном queryset
        # (select_related проекта/версий + счётчики открытых замечаний),
        # иначе каждая строка реестра порождает N+1 запросов через
        # document_actions → can_transition/_has_open_issues.
        kwargs['queryset'] = self.get_queryset()
        return kwargs

    def _build_filter_chips(self, saved_filters, get_params, sections, active_zone):
        """Спринт 8: чипы активных фильтров для строки «Отфильтровано».

        Учитываются и GET-параметры, и фильтры сохранённого вида
        (get_filterset_kwargs подмешивает их в форму).
        """
        chips = []
        for param, label, choices in FILTER_CHIP_FIELDS:
            value = (get_params.get(param) or '').strip()
            if not value and saved_filters:
                value = str(saved_filters.get(param) or '').strip()
            if not value:
                continue
            if param == 'section':
                section = next(
                    (s for s in sections if str(s.pk) == value), None,
                )
                display = (
                    f'{section.code} — {section.name}' if section else value
                )
            elif choices:
                display = dict(choices).get(value, value)
            else:
                display = value
            chips.append({
                'param': param,
                'label': label,
                'display': display,
            })
        if active_zone:
            chips.insert(0, {
                'param': 'zone',
                'label': 'Зона',
                'display': dict(Folder.ZONE_CHOICES).get(
                    active_zone, active_zone,
                ),
            })
        return chips

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        visible = set(self._get_view_state()[2]['columns'])
        kwargs['exclude'] = [
            name
            for name in DocumentTable.base_columns
            if name not in ALWAYS_VISIBLE_TABLE_COLUMNS
            and DOCUMENT_TABLE_COLUMN_MAP.get(name, name) not in visible
        ]
        return kwargs

    def get_template_names(self):
        if getattr(self.request, 'htmx', False):
            return ['storage/_documents_panel.html']
        return [self.template_name]

    def get_table(self, **kwargs):
        table = super().get_table(**kwargs)
        config = self._get_view_state()[2]

        names = {column.name for column in table.columns}
        ordering = []
        for field in config['sort']:
            name = DOCUMENT_SORT_COLUMN_MAP[field.lstrip('-')]
            if name in names:
                prefix = '-' if field.startswith('-') else ''
                ordering.append(f'{prefix}{name}')
        if self.request.GET.get('apply') or not self.request.GET.get('sort'):
            table.order_by = ordering

        raw_highlight = (self.request.GET.get('highlight') or '').strip()
        table.highlight_id = int(raw_highlight) if raw_highlight.isdigit() else None

        return table

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        project = self._get_project()
        document_views, selected_view, view_config = self._get_view_state()

        can_manage_participants = can_manage_project_participants(user, project)
        active_tab = 'documents'
        requested_tab = self.request.GET.get('tab')
        if can_manage_participants and requested_tab in ('participants', 'trash'):
            active_tab = requested_tab

        sections = list(get_sections_with_counts(user, project))
        all_roots = list(get_folder_tree(user, project))
        project_folders = [
            folder
            for folder in all_roots
            if folder.section_id is None
            and not folder.is_system_zone
        ]
        folder_roots = [
            folder for folder in all_roots if folder.section_id is not None
        ]
        all_folders = [
            folder
            for folder in get_flat_folders(user, project)
            if not (
                folder.parent_id is None
                and folder.section_id is None
                and folder.is_system_zone
            )
        ]
        document_move_folders = list(
            Folder.objects.filter(
                project=project,
                is_deleted=False,
            )
            .exclude(is_system_zone=True)
            # Спринт 8: project/parent нужны check_write_permission,
            # check_zone_permission и get_folder_mark_id на каждую папку —
            # без select_related это ~130 запросов N+1 на страницу.
            .select_related(
                'section',
                'section__project',
                'project',
                'parent',
                'parent__parent',
                'parent__parent__parent',
                'parent__parent__parent__parent',
                'parent__parent__parent__parent__parent',
            )
            .order_by('order', 'name')
        )
        for folder in document_move_folders:
            folder.mark_id = get_folder_mark_id(folder)
            folder.can_move_to = bool(
                folder.section_id
                and check_write_permission(user, folder.section)
                and check_zone_permission(user, folder, folder.section)
            )

        move_folder_by_id = {
            folder.pk: folder for folder in document_move_folders
        }
        for folder in document_move_folders:
            names = []
            current = folder
            while current is not None:
                label = (
                    f'{current.code} {current.name}'.strip()
                    if current.code
                    else current.name
                )
                names.append(label)
                current = move_folder_by_id.get(current.parent_id)
            folder.tree_label = ' / '.join(reversed(names))

            ancestors = []
            parent_id = folder.parent_id
            while parent_id:
                ancestors.append(str(parent_id))
                parent = move_folder_by_id.get(parent_id)
                parent_id = parent.parent_id if parent is not None else None
            folder.ancestor_ids = ','.join(ancestors)

        folder_move_folders = document_move_folders
        folders_by_section = {}
        for folder in folder_roots:
            folders_by_section.setdefault(folder.section_id, []).append(folder)
        for section in sections:
            section.folder_nodes = folders_by_section.get(section.pk, [])

        # Секции, представленные папками внутри суперпапок ИРД/РИИ/ПД/РД,
        # не дублируем отдельными узлами дерева. Ручные секции остаются видны.
        superfolder_ids = {
            folder.pk
            for folder in project_folders
            if folder.name in ROOT_SUPERFOLDER_NAMES
        }
        parent_by_id = {folder.pk: folder.parent_id for folder in all_folders}
        represented_section_ids = set()
        for folder in all_folders:
            if not folder.section_id:
                continue
            current_id = folder.pk
            seen = set()
            while current_id and current_id not in seen:
                if current_id in superfolder_ids:
                    represented_section_ids.add(folder.section_id)
                    break
                seen.add(current_id)
                current_id = parent_by_id.get(current_id)
        section_nodes = [
            section
            for section in sections
            if section.pk not in represented_section_ids
        ]

        selected_folder = self._get_selected_folder()
        selected_section = self._get_selected_section()

        column_defs = _document_column_defs(view_config)
        active_zone = self._get_active_zone()
        documents = get_project_documents_for_view(
            user,
            project,
            view_config,
            folder=selected_folder,
            zone=active_zone or None,
        )
        active_search = (self.request.GET.get('q') or '').strip()
        if active_search:
            needle = active_search.lower()
            documents = [
                document
                for document in documents
                if needle in document['code'].lower()
                or needle in document['name'].lower()
                or needle in str(document.get('author', '')).lower()
            ]

        active_filter_chips = self._build_filter_chips(
            view_config['filters'],
            self.request.GET,
            sections,
            active_zone,
        )

        highlight_id = None
        raw_highlight = (self.request.GET.get('highlight') or '').strip()
        if raw_highlight.isdigit():
            highlight_id = int(raw_highlight)

        if selected_folder is not None:
            upload_target_name = selected_folder.name
        elif selected_section is not None:
            upload_target_name = f'раздел {selected_section.code}'
        else:
            upload_target_name = 'выбранный раздел'

        context.update({
            'project': project,
            'sections': sections,
            'section_nodes': section_nodes,
            'folder_tree': folder_roots,
            'project_folders': project_folders,
            'all_folders': all_folders,
            'document_move_folders': document_move_folders,
            'folder_move_folders': folder_move_folders,
            'documents': documents,
            'document_views': document_views,
            'selected_view': selected_view,
            'selected_folder': selected_folder,
            'selected_section': selected_section,
            'breadcrumbs': _project_breadcrumbs(
                project,
                section=selected_section,
                folder=selected_folder,
            ),
            'upload_target_name': upload_target_name,
            'highlight_id': highlight_id,
            'highlight_seconds': HIGHLIGHT_ROW_SECONDS,
            'active_search': active_search,
            'active_filter_chips': active_filter_chips,
            'view_config': view_config,
            'widths_json': json.dumps(view_config['widths']),
            'active_filters': view_config['filters'],
            'column_defs': column_defs,
            'visible_columns': [column for column in column_defs if column['visible']],
            'active_sort': (
                self.request.GET.get('sort')
                or (view_config['sort'][0] if view_config['sort'] else '')
            ),
            'active_tab': active_tab,
            'can_manage_participants': can_manage_participants,
            'can_manage_structure': can_manage_participants,
            'can_purge_trash': can_purge_trash(user, project),
            'doc_type_choices': Document.DOC_TYPE_CHOICES,
            'status_choices': Document.STATUS_CHOICES,
            'active_zone': active_zone,
            'zone_segments': [
                {
                    'code': code,
                    'label': label,
                    'icon': ZONE_WORKFLOW_ICONS[code],
                }
                for code, label in Folder.ZONE_CHOICES
            ],
            'zone_counts': get_zone_counts(user, project),
        })

        # Счётчики замечаний проекта — «нервная система» для ГИПа:
        # видно из тулбара таблицы, сколько требует внимания прямо сейчас.
        # (Тот же смысл, что и в реестре: issues:issue_registry.)
        active_issue_statuses = ('NEW', 'IN_PROGRESS', 'DISCUSSION', 'REJECTED')
        context['issue_counts'] = Issue.objects.filter(
            document__project=project,
            document__in=get_accessible_documents(user),
        ).aggregate(
            total=Count('pk'),
            open=Count('pk', filter=~Q(status__in=('CLOSED', 'CANCELLED'))),
            overdue=Count(
                'pk',
                filter=Q(due_date__isnull=False)
                & Q(due_date__lt=timezone.localdate())
                & Q(status__in=active_issue_statuses),
            ),
        )

        if can_manage_participants:
            context['participants'] = list_project_participants(user, project)
            context['membership_role_choices'] = ProjectMembership.ROLE_IN_PROJECT_CHOICES

        if active_tab == 'trash':
            context['trash'] = get_trash(user, project)

        return context


def _folder_archive_segments(folder, base_folder=None):
    """Возвращает относительные сегменты пути папки для ZIP."""
    segments = []
    current = folder
    while current is not None:
        if base_folder is not None and current.pk == base_folder.pk:
            break
        segments.append(get_valid_filename(current.name) or f'folder-{current.pk}')
        current = current.parent
    return list(reversed(segments))


def _zip_documents(documents, filename='documents.zip', base_folder=None):
    """Собирает ZIP из текущих версий документов с сохранением папок."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for document in documents:
            version = document.current_version
            if version is None or not version.file:
                continue
            extension = version.file.name.rsplit('.', 1)[-1]
            if extension and len(extension) <= 8:
                extension = f'.{extension}'
            else:
                extension = ''
            document_filename = (
                get_valid_filename(document.code) or f'document-{document.pk}'
            )
            archive_name = '/'.join(
                _folder_archive_segments(document.folder, base_folder)
                + [f'{document_filename}_v{version.version_number}{extension}']
            )
            try:
                with version.file.open('rb') as source:
                    archive.writestr(archive_name, source.read())
            except (FileNotFoundError, OSError):
                continue

    response = HttpResponse(buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


class ProjectCreateView(LoginRequiredMixin, View):
    def post(self, request):
        if not can_create_project(request.user):
            return HttpResponseForbidden('Нет прав на создание проекта')

        company = get_user_company(request.user)
        if company is None and request.user.is_superuser:
            company = Company.objects.first()
        if company is None:
            messages.error(request, 'Не удалось определить компанию пользователя.')
            return redirect('storage:project_list')

        gip = None
        gip_id = request.POST.get('gip')
        if gip_id:
            gip = get_user_model().objects.filter(pk=gip_id).first()

        try:
            project = create_project_from_template(
                company=company,
                code=request.POST.get('code', '').strip(),
                name=request.POST.get('name', '').strip(),
                customer=request.POST.get('customer', '').strip(),
                gip=gip,
                created_by=request.user,
                template_key=request.POST.get('template_key') or None,
                base_code=request.POST.get('base_code', '').strip(),
            )
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect('storage:project_list')

        if getattr(project, '_restored_from_trash', False):
            messages.success(
                request,
                f'Проект «{project.name}» восстановлен из корзины и обновлён.',
            )
        else:
            messages.success(request, f'Проект «{project.name}» создан.')
        return redirect('storage:project_detail', pk=project.pk)


class ProjectEditView(LoginRequiredMixin, View):
    """Редактирование карточки проекта (админ, ГИП своего проекта, суперюзер)."""

    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        gip = None
        gip_id = (request.POST.get('gip') or '').strip()
        if gip_id:
            gip = get_user_model().objects.filter(pk=gip_id).first()
            if gip is None:
                messages.error(request, 'ГИП не найден.')
                return redirect('storage:project_list')

        try:
            update_project(
                request.user,
                project,
                code=request.POST.get('code', ''),
                name=request.POST.get('name', ''),
                customer=request.POST.get('customer', ''),
                base_code=request.POST.get('base_code', ''),
                status=request.POST.get('status') or project.status,
                gip=gip,
            )
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'Проект «{project.name}» обновлён.')
        return redirect('storage:project_list')


class ProjectArchiveView(LoginRequiredMixin, View):
    """Архивирование проекта (только админ/суперюзер)."""

    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        try:
            archive_project(request.user, project)
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'Проект «{project.name}» перемещён в архив.')
        return redirect('storage:project_list')


class ProjectDeleteView(LoginRequiredMixin, View):
    """Мягкое удаление проекта (только админ/суперюзер)."""

    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        try:
            delete_project(request.user, project)
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'Проект «{project.name}» удалён.')
        return redirect('storage:project_list')


class ProjectRestoreView(LoginRequiredMixin, View):
    """Восстанавливает проект из корзины (только админ/суперюзер)."""

    def post(self, request, project_id):
        project = get_object_or_404(
            get_deleted_projects(request.user),
            pk=project_id,
        )
        try:
            restore_project(request.user, project)
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'Проект «{project.name}» восстановлен.')
        return redirect('storage:project_list')


class ProjectResetStructureView(LoginRequiredMixin, View):
    """Пересоздаёт папки проекта по сохранённому шаблону."""

    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        try:
            stats = reset_project_structure(request.user, project)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                'Структура пересоздана: '
                f"папок — {stats['created_folders']}, "
                f"разделов — {stats['created_sections']}.",
            )
        return redirect('storage:project_list')


class SectionCreateView(LoginRequiredMixin, View):
    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        try:
            order = request.POST.get('order')
            if not order:
                max_order = Section.objects.filter(project=project).aggregate(
                    Max('order')
                )['order__max'] or 0
                order = max_order + 1
            create_section(
                project=project,
                code=request.POST.get('code', ''),
                name=request.POST.get('name', ''),
                order=order,
                user=request.user,
            )
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Раздел создан.')
        return redirect('storage:project_detail', pk=project.pk)


class SectionRenameView(LoginRequiredMixin, View):
    def post(self, request, section_id):
        section = get_object_or_404(
            Section.objects.select_related('project'),
            pk=section_id,
            project__in=get_accessible_projects(request.user),
        )
        try:
            rename_section(
                request.user,
                section,
                request.POST.get('name', ''),
                request.POST.get('code'),
            )
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Раздел переименован.')
        return redirect('storage:project_detail', pk=section.project_id)


class FolderCreateView(LoginRequiredMixin, View):
    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        section = None
        section_id = request.POST.get('section')
        if section_id:
            section = Section.objects.filter(project=project, pk=section_id).first()
            if section is None:
                messages.error(request, 'Раздел не найден.')
                return redirect('storage:project_detail', pk=project.pk)
        parent = None
        parent_id = request.POST.get('parent')
        if parent_id:
            parent = Folder.objects.filter(project=project, pk=parent_id).first()
            if parent is None:
                messages.error(request, 'Родительская папка не найдена.')
                return redirect('storage:project_detail', pk=project.pk)

        try:
            create_folder(
                user=request.user,
                project=project,
                name=request.POST.get('name', ''),
                section=section,
                parent=parent,
                code=request.POST.get('code', ''),
            )
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Папка создана.')
        return redirect('storage:project_detail', pk=project.pk)


class FolderCreateFormView(LoginRequiredMixin, TemplateView):
    """HTML-фрагмент формы создания папки для Bootstrap modal (HTMX)."""

    template_name = 'storage/_folder_create_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = get_object_or_404(
            get_accessible_projects(self.request.user),
            pk=self.kwargs['project_id'],
        )
        if not can_manage_project_participants(self.request.user, project):
            raise PermissionDenied("Недостаточно прав для создания папки")

        parent = None
        parent_id = (self.request.GET.get('parent') or '').strip()
        if parent_id.isdigit():
            parent = Folder.objects.filter(
                project=project,
                pk=int(parent_id),
                is_deleted=False,
            ).select_related('section').first()

        section = parent.section if parent is not None else None
        section_id = (self.request.GET.get('section') or '').strip()
        if section is None and section_id.isdigit():
            section = Section.objects.filter(
                project=project,
                pk=int(section_id),
            ).first()

        context.update({
            'project': project,
            'parent': parent,
            'section': section,
        })
        return context


class FolderRenameFormView(LoginRequiredMixin, TemplateView):
    """HTML-фрагмент формы переименования папки для Bootstrap modal (HTMX)."""

    template_name = 'storage/_folder_rename_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        folder = get_object_or_404(
            Folder.objects.select_related('project', 'section', 'parent'),
            pk=self.kwargs['folder_id'],
            project__in=get_accessible_projects(self.request.user),
        )
        if not can_manage_project_participants(self.request.user, folder.project):
            raise PermissionDenied("Недостаточно прав для переименования папки")
        context.update({'folder': folder, 'project': folder.project})
        return context


class FolderRenameView(LoginRequiredMixin, View):
    def post(self, request, folder_id):
        folder = get_object_or_404(
            Folder,
            pk=folder_id,
            project__in=get_accessible_projects(request.user),
        )
        try:
            rename_folder(
                request.user,
                folder,
                request.POST.get('name', ''),
                request.POST.get('code'),
            )
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Папка переименована.')
        return redirect('storage:project_detail', pk=folder.project_id)


class FolderDeleteView(LoginRequiredMixin, View):
    def post(self, request, folder_id):
        folder = get_object_or_404(
            Folder,
            pk=folder_id,
            project__in=get_accessible_projects(request.user),
        )
        try:
            delete_folder(request.user, folder)
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Папка перемещена в корзину.')
        return redirect('storage:project_detail', pk=folder.project_id)


class FolderDownloadView(LoginRequiredMixin, View):
    def get(self, request, folder_id):
        folder = get_object_or_404(
            Folder,
            pk=folder_id,
            project__in=get_accessible_projects(request.user),
        )
        try:
            documents = list(get_folder_documents(request.user, folder))
        except (PermissionDenied, ValueError) as exc:
            return HttpResponseForbidden(str(exc))
        return _zip_documents(documents, 'folder.zip', base_folder=folder)


class DocumentShareLinkView(LoginRequiredMixin, View):
    """Создаёт/продлевает публичную ссылку. Только POST с CSRF."""

    def post(self, request, document_id):
        document = get_object_or_404(
            get_accessible_documents(request.user),
            pk=document_id,
            is_deleted=False,
        )
        try:
            token = generate_share_link(request.user, document)
        except (PermissionDenied, ValueError) as exc:
            return JsonResponse({'error': str(exc)}, status=403)
        url = request.build_absolute_uri(
            reverse('storage:document_detail', args=[document.pk])
        )
        return JsonResponse({
            'token': token,
            'url': f'{url}?share={token}',
            'expires_at': (
                document.public_link_expires_at.isoformat()
                if document.public_link_expires_at else None
            ),
        })


class DocumentShareRevokeView(LoginRequiredMixin, View):
    """Отзывает публичную ссылку. Только POST с CSRF."""

    def post(self, request, document_id):
        document = get_object_or_404(
            get_accessible_documents(request.user),
            pk=document_id,
            is_deleted=False,
        )
        try:
            revoke_share_link(request.user, document)
        except (PermissionDenied, ValueError) as exc:
            return JsonResponse({'status': 'error', 'error': str(exc)}, status=403)
        return JsonResponse({'status': 'ok'})


class DocumentMoveView(LoginRequiredMixin, View):
    """AJAX-перемещение документа при drag-and-drop на папку или раздел."""

    def post(self, request, document_id):
        document = get_object_or_404(
            get_accessible_documents(request.user),
            pk=document_id,
            is_deleted=False,
        )
        folder_id = (request.POST.get('folder_id') or '').strip()
        section_id = (request.POST.get('section_id') or '').strip()
        allow_section_change = request.POST.get('allow_section_change') in (
            '1', 'true', 'on',
        )

        try:
            if folder_id:
                folder = Folder.objects.filter(
                    project=document.project,
                    pk=folder_id,
                    is_deleted=False,
                ).first()
                if folder is None:
                    raise ValueError('Папка не найдена.')
                move_document(
                    request.user,
                    document,
                    folder,
                    allow_section_change=allow_section_change,
                )
            elif section_id:
                section = get_object_or_404(
                    get_accessible_sections(request.user, document.project),
                    pk=section_id,
                )
                # Спринт 7: сброс в корень СВОЕГО раздела (жест «вынести из
                # папки») разрешён без явного подтверждения — марка не меняется.
                # Кросс-разделный перенос требует allow_section_change=1
                # (кнопка «Сменить раздел» или confirm в JS при DnD).
                if section.pk != document.section_id and not allow_section_change:
                    raise ValueError(
                        "Смена раздела через обычное перемещение запрещена"
                    )
                move_document_to_section(request.user, document, section)
            else:
                move_document(
                    request.user,
                    document,
                    None,
                    allow_section_change=allow_section_change,
                )
        except PermissionDenied as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=403,
            )
        except ValidationError as exc:
            return JsonResponse(
                {'status': 'error', 'error': ' '.join(exc.messages)},
                status=400,
            )
        except ValueError as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=400,
            )

        return JsonResponse({
            'status': 'ok',
            'document_id': document.pk,
            'folder_id': document.folder_id,
            'section_id': document.section_id,
        })


def _workflow_actions_payload(user, document):
    actions = document_actions(user, document)
    for action in actions:
        if action.get('kind') == 'internal':
            action['url'] = reverse(
                'storage:document_internal_status_update',
                args=[document.pk],
            )
        else:
            action['url'] = reverse(
                'storage:document_workflow',
                args=[document.pk],
            )
    return actions


class DocumentWorkflowView(LoginRequiredMixin, View):
    """Workflow зон ISO 19650 (Волна 3): «Подать на согласование» и т.д.

    POST action=submit_review|return_wip|publish|archive.
    Возвращает JSON с новой зоной/статусом и списком доступных действий —
    строка таблицы обновляется точечно, без перезагрузки.
    """

    def post(self, request, document_id):
        document = get_object_or_404(
            get_accessible_documents(request.user),
            pk=document_id,
            is_deleted=False,
        )
        action = (request.POST.get('action') or '').strip()
        try:
            transition_document_zone(request.user, document, action)
        except PermissionDenied as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=403,
            )
        except ValidationError as exc:
            return JsonResponse(
                {'status': 'error', 'error': ' '.join(exc.messages)},
                status=400,
            )
        except ValueError as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=400,
            )

        effective_zone = _effective_zone(document)
        zone_name = dict(Folder.ZONE_CHOICES).get(effective_zone, effective_zone)
        return JsonResponse({
            'status': 'ok',
            'document_id': document.pk,
            'zone_code': effective_zone,
            'zone_name': zone_name,
            'status_code': document.status,
            'status_display': document.get_status_display(),
            'suitability_code': document.suitability_code,
            'suitability_display': document.get_suitability_code_display(),
            'internal_status': document.internal_status,
            'internal_display': document.get_internal_status_display(),
            'open_issues': _open_issues_count(document),
            'message': '{} — {}'.format(document.code, zone_name),
            'actions': _workflow_actions_payload(request.user, document),
        })


class DocumentInternalStatusView(LoginRequiredMixin, View):
    """Выполнение внутреннего workflow-действия WIP-документа."""

    def post(self, request, document_id):
        document = get_object_or_404(
            get_accessible_documents(request.user),
            pk=document_id,
            is_deleted=False,
        )
        action = (request.POST.get('action') or '').strip()
        try:
            update_internal_status(request.user, document, action)
        except PermissionDenied as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=403,
            )
        except ValidationError as exc:
            return JsonResponse(
                {'status': 'error', 'error': ' '.join(exc.messages)},
                status=400,
            )
        except ValueError as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=400,
            )

        effective_zone = _effective_zone(document)
        zone_name = dict(Folder.ZONE_CHOICES).get(effective_zone, effective_zone)
        return JsonResponse({
            'status': 'ok',
            'document_id': document.pk,
            'zone_code': effective_zone,
            'zone_name': zone_name,
            'status_code': document.status,
            'status_display': document.get_status_display(),
            'suitability_code': document.suitability_code,
            'suitability_display': document.get_suitability_code_display(),
            'internal_status': document.internal_status,
            'internal_display': document.get_internal_status_display(),
            'open_issues': _open_issues_count(document),
            'message': '{} — {}'.format(document.code, zone_name),
            'actions': _workflow_actions_payload(request.user, document),
        })


class PaletteSearchView(LoginRequiredMixin, View):
    """Командная палитра (Волна 3): JSON-поиск по Ctrl+K.

    Ищет по проектам, документам и замечаниям, доступным пользователю.
    """

    def get(self, request):
        query = (request.GET.get('q') or '').strip()
        if len(query) < 2:
            return JsonResponse(
                {'projects': [], 'documents': [], 'issues': []}
            )

        projects = (
            get_accessible_projects(request.user)
            .filter(Q(code__icontains=query) | Q(name__icontains=query))
            .order_by('code')[:5]
        )
        documents = (
            get_readable_documents(request.user)
            .filter(Q(code__icontains=query) | Q(name__icontains=query))
            .select_related('project')
            .order_by('code')[:6]
        )
        issues = (
            Issue.objects.filter(
                document__in=get_readable_documents(request.user),
                document__is_deleted=False,
            )
            .filter(
                Q(description__icontains=query)
                | Q(document__code__icontains=query)
            )
            .select_related('document')[:6]
        )

        def _doc_url(document):
            return reverse('storage:document_detail', args=[document.pk])

        return JsonResponse({
            'projects': [
                {
                    'title': '{} — {}'.format(p.code, p.name),
                    'url': reverse('storage:project_detail', args=[p.pk]),
                }
                for p in projects
            ],
            'documents': [
                {
                    'title': '{} — {}'.format(d.code, d.name),
                    'meta': d.project.code,
                    'url': _doc_url(d),
                }
                for d in documents
            ],
            'issues': [
                {
                    'title': 'Замечание №{}: {}'.format(
                        i.pk, (i.description or '')[:80]
                    ),
                    'meta': i.document.code,
                    'url': _doc_url(i.document),
                }
                for i in issues
            ],
        })


class ServiceWorkerView(View):
    """Отдаёт sw.js из корня сайта — PWA для площадки (Волна 3).

    Scope сервис-воркера определяется расположением файла, поэтому
    маршрут обязан жить на ``/sw.js``, а не в ``/static/js/``.

    Спринт 3 (P2): в отдаёмый файл подставляется версия кэша, собранная
    из хэша содержимого пре-кэшируемых файлов. Поменялась статика —
    изменился текст sw.js — браузер сам переустановит воркер и соберёт
    свежий кэш; ручной бамп CACHE_NAME не нужен.
    """

    #: маркер в static/js/sw.js, заменяемый на версию кэша
    VERSION_MARKER = '__CACHE_VERSION__'

    def get(self, request):
        sw_path = finders.find('js/sw.js')
        if sw_path is None:
            raise Http404('sw.js не найден в static')
        with open(sw_path, encoding='utf-8') as handle:
            source = handle.read()
        if self.VERSION_MARKER in source:
            source = source.replace(
                self.VERSION_MARKER, self._cache_version(sw_path, source),
            )
        response = HttpResponse(source, content_type='application/javascript')
        response['Service-Worker-Allowed'] = '/'
        return response

    @classmethod
    def _cache_version(cls, sw_path, sw_source):
        """MD5-хэш пре-кэша: содержимое файлов из PRECACHE_URLS + шаблон /offline/.

        Считается один раз на процесс (статика меняется только при выкатке);
        файлы, не найденные через staticfiles, пропускаются — install
        всё равно делает allSettled.
        """
        cached = getattr(cls, '_cache_version_value', None)
        if cached:
            return cached

        digest = hashlib.md5()
        url_list = re.search(
            r'const PRECACHE_URLS = \[(.*?)\]', sw_source, re.S,
        )
        urls = re.findall(r"'([^']+)'", url_list.group(1)) if url_list else []
        for url in urls:
            if not url.startswith('/static/'):
                continue
            static_path = finders.find(url.removeprefix('/static/'))
            if not static_path:
                continue
            digest.update(url.encode('utf-8'))
            try:
                with open(static_path, 'rb') as handle:
                    digest.update(handle.read())
            except OSError:
                continue
        try:
            template = get_template('storage/offline.html')
            digest.update(template.template.source.encode('utf-8'))
        except Exception:
            pass

        version = digest.hexdigest()[:10]
        cls._cache_version_value = version
        return version


class OfflineView(TemplateView):
    """Офлайн-заглушка для PWA (кэшируется service worker)."""

    template_name = 'storage/offline.html'


class DocumentDeleteView(LoginRequiredMixin, View):
    """AJAX-отправка документа в корзину из контекстного меню."""

    def post(self, request, document_id):
        document = get_object_or_404(
            get_accessible_documents(request.user),
            pk=document_id,
            is_deleted=False,
        )
        try:
            delete_document(request.user, document)
        except PermissionDenied as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=403,
            )
        except ValueError as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=400,
            )
        return JsonResponse({
            'status': 'ok',
            'document_id': document.pk,
        })


class DocumentCopyView(LoginRequiredMixin, View):
    """AJAX-копирование документа при drop с зажатым Ctrl."""

    def post(self, request, document_id):
        document = get_object_or_404(
            get_accessible_documents(request.user),
            pk=document_id,
            is_deleted=False,
        )
        folder_id = (request.POST.get('folder_id') or '').strip()
        section_id = (request.POST.get('section_id') or '').strip()
        folder = None
        section = None

        try:
            if folder_id:
                folder = Folder.objects.filter(
                    project=document.project,
                    pk=folder_id,
                    is_deleted=False,
                ).first()
                if folder is None:
                    raise ValueError('Папка не найдена.')
            elif section_id:
                section = get_object_or_404(
                    get_accessible_sections(request.user, document.project),
                    pk=section_id,
                )
            else:
                folder = document.folder
                section = document.section

            copied = copy_document(
                request.user,
                document,
                folder,
                section,
                LocalFileStorage(),
            )
        except PermissionDenied as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=403,
            )
        except ValueError as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=400,
            )

        return JsonResponse({
            'status': 'ok',
            'document_id': copied.pk,
            'code': copied.code,
            'folder_id': copied.folder_id,
            'section_id': copied.section_id,
        })


class FolderMoveView(LoginRequiredMixin, View):
    """AJAX-перемещение папки в другую папку или раздел."""

    def post(self, request, folder_id):
        folder = get_object_or_404(
            Folder.objects.select_related('project', 'section', 'parent'),
            pk=folder_id,
            project__in=get_accessible_projects(request.user),
            is_deleted=False,
        )
        parent_id = (request.POST.get('parent_id') or '').strip()
        section_id = (request.POST.get('section_id') or '').strip()
        allow_section_change = request.POST.get('allow_section_change') in (
            '1', 'true', 'on',
        )
        parent = None
        section = None

        try:
            if parent_id:
                parent = Folder.objects.filter(
                    project=folder.project,
                    pk=parent_id,
                    is_deleted=False,
                ).first()
                if parent is None:
                    raise ValueError('Целевая папка не найдена.')
            elif section_id:
                section = Section.objects.filter(
                    project=folder.project,
                    pk=section_id,
                ).first()
                if section is None:
                    raise ValueError('Целевой раздел не найден.')

            move_folder(
                request.user,
                folder,
                parent=parent,
                section=section,
                to_project_root=not parent_id and not section_id,
                allow_section_change=allow_section_change,
            )
        except PermissionDenied as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=403,
            )
        except ValueError as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=400,
            )

        return JsonResponse({
            'status': 'ok',
            'folder_id': folder.pk,
            'parent_id': folder.parent_id,
            'section_id': folder.section_id,
        })


class FolderReorderView(LoginRequiredMixin, View):
    """AJAX-переупорядочивание папок в дереве."""

    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        try:
            updated = reorder_folders(
                request.user,
                project,
                request.POST.getlist('ids'),
            )
        except PermissionDenied as exc:
            return JsonResponse(
                {'status': 'error', 'error': str(exc)},
                status=403,
            )
        return JsonResponse({'status': 'ok', 'updated': updated})


class DocumentBulkActionView(LoginRequiredMixin, View):
    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        document_ids = request.POST.getlist('document_ids')
        documents = list(
            get_accessible_documents(request.user).filter(
                project=project,
                pk__in=document_ids,
                is_deleted=False,
            )
        )
        if not documents:
            messages.error(request, 'Не выбраны документы.')
            return redirect('storage:project_detail', pk=project.pk)

        action = request.POST.get('action')
        try:
            if action == 'download':
                return _zip_documents(documents)
            if action == 'move':
                folder = None
                folder_id = request.POST.get('folder')
                if folder_id:
                    folder = Folder.objects.filter(
                        project=project,
                        pk=folder_id,
                        is_deleted=False,
                    ).first()
                    if folder is None:
                        raise ValueError('Папка не найдена.')
                move_documents(request.user, documents, folder)
                messages.success(request, f'Перемещено документов: {len(documents)}.')
            elif action == 'delete':
                delete_documents(request.user, documents)
                messages.success(request, f'В корзину отправлено: {len(documents)}.')
            else:
                messages.error(request, 'Неизвестное действие.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))

        return redirect('storage:project_detail', pk=project.pk)


class TrashRestoreView(LoginRequiredMixin, View):
    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        item_type = request.POST.get('item_type')
        item_id = request.POST.get('item_id')
        try:
            if item_type == 'document':
                document = get_object_or_404(
                    Document,
                    pk=item_id,
                    project=project,
                    is_deleted=True,
                )
                restore_document(request.user, document)
            elif item_type == 'folder':
                folder = get_object_or_404(
                    Folder,
                    pk=item_id,
                    project=project,
                    is_deleted=True,
                )
                restore_folder(request.user, folder)
            else:
                raise ValueError('Неизвестный тип элемента корзины')
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Элемент восстановлен.')
        return redirect(f"{reverse('storage:project_detail', args=[project.pk])}?tab=trash")


class TrashClearView(LoginRequiredMixin, View):
    """Безвозвратная очистка корзины проекта (только администратор)."""

    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        try:
            result = empty_trash(request.user, project, LocalFileStorage())
        except PermissionDenied as exc:
            return HttpResponseForbidden(str(exc))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                'Корзина очищена: удалено документов — '
                f"{result['documents']}, папок — {result['folders']}.",
            )
        return redirect(f"{reverse('storage:project_detail', args=[project.pk])}?tab=trash")


class DocumentViewSaveView(LoginRequiredMixin, View):
    def post(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )

        raw_widths = (request.POST.get('widths') or '').strip()
        widths = {}
        if raw_widths:
            try:
                widths = json.loads(raw_widths)
            except (TypeError, ValueError):
                messages.error(request, 'Некорректные ширины столбцов.')
                return redirect('storage:project_detail', pk=project.pk)

        config = {
            'columns': request.POST.getlist('columns'),
            'widths': widths,
            'filters': {
                'section': request.POST.get('section', ''),
                'doc_type': request.POST.get('doc_type', ''),
                'status': request.POST.get('status', ''),
            },
            'sort': request.POST.get('sort', ''),
        }

        try:
            view = save_document_view(
                user=request.user,
                project=project,
                name=request.POST.get('name', ''),
                config=config,
                is_shared=request.POST.get('is_shared') == 'on',
            )
        except PermissionDenied as exc:
            return HttpResponseForbidden(str(exc))
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('storage:project_detail', pk=project.pk)

        messages.success(request, f'Вид «{view.name}» сохранён.')
        project_url = reverse('storage:project_detail', args=[project.pk])
        return redirect(f'{project_url}?view={view.pk}')


class MembershipRoleUpdateView(LoginRequiredMixin, View):
    def post(self, request, project_id, membership_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        try:
            membership = get_project_membership(
                request.user,
                project,
                membership_id,
            )
            if membership is None:
                raise Http404("Участник проекта не найден")

            update_project_member_role(
                request.user,
                membership,
                request.POST.get('role', ''),
            )
        except PermissionDenied as exc:
            return HttpResponseForbidden(str(exc))
        except ValueError as exc:
            messages.error(request, str(exc))
            project_url = reverse('storage:project_detail', args=[project.pk])
            return redirect(f'{project_url}?tab=participants')

        messages.success(
            request,
            f'Роль участника {membership.user.get_full_name() or membership.user.username} обновлена.',
        )
        project_url = reverse('storage:project_detail', args=[project.pk])
        return redirect(f'{project_url}?tab=participants')


class SearchView(LoginRequiredMixin, TemplateView):
    template_name = 'storage/search.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        project = get_object_or_404(
            get_accessible_projects(user),
            pk=self.kwargs['project_id'],
        )
        sections = get_accessible_sections(user, project)

        query = (self.request.GET.get('q') or '').strip()

        selected_section = None
        section_id = (self.request.GET.get('section') or '').strip()
        if section_id:
            try:
                selected_section = sections.filter(pk=int(section_id)).first()
            except (TypeError, ValueError):
                selected_section = None

        doc_type_choices = Document.DOC_TYPE_CHOICES
        doc_type = (self.request.GET.get('doc_type') or '').strip()
        if doc_type not in dict(doc_type_choices):
            doc_type = ''

        status_choices = Document.STATUS_CHOICES
        status = (self.request.GET.get('status') or '').strip()
        if status not in dict(status_choices):
            status = ''

        documents = search_documents(
            user=user,
            project=project,
            query=query,
            section=selected_section,
            doc_type=doc_type or None,
            status=status or None,
        )

        # Спринт 7: «Недавние» показываем только в пустом поиске —
        # там это отправная точка, а не шум над таблицей проекта.
        recent_documents = (
            _recent_documents(self.request, user, project)
            if not query
            else []
        )

        context.update({
            'project': project,
            'documents': documents,
            'query': query,
            'sections': sections,
            'selected_section': selected_section,
            'selected_doc_type': doc_type,
            'selected_status': status,
            'doc_type_choices': doc_type_choices,
            'status_choices': status_choices,
            'recent_documents': recent_documents,
        })
        return context


def _issue_payload(issue):
    document_url = reverse(
        'storage:document_detail', args=[issue.document_id]
    )
    return {
        'id': issue.pk,
        'page_number': issue.page_number,
        'x_ratio': issue.x_ratio,
        'y_ratio': issue.y_ratio,
        'x1_ratio': issue.x1_ratio,
        'y1_ratio': issue.y1_ratio,
        'x2_ratio': issue.x2_ratio,
        'y2_ratio': issue.y2_ratio,
        'status': issue.status,
        'status_label': issue.get_status_display(),
        'description': issue.description,
        'author': issue.author.get_full_name() or issue.author.username,
        'assignee': (
            issue.assignee.get_full_name() or issue.assignee.username
            if issue.assignee else ''
        ),
        'version': issue.version.version_number if issue.version else None,
        'department': issue.department,
        'project_organization': issue.project_organization,
        'due_date': issue.due_date.isoformat() if issue.due_date else '',
        'actual_fix_date': (
            issue.actual_fix_date.isoformat() if issue.actual_fix_date else ''
        ),
        'revision': issue.revision,
        'answer_text': issue.answer_text,
        'crop_image_url': issue.crop_image.url if issue.crop_image else '',
        'document_url': document_url,
        'status_options': [],
    }


class DocumentDetailView(LoginRequiredMixin, DetailView):
    model = Document
    template_name = 'storage/document_detail.html'
    context_object_name = 'document'

    def dispatch(self, request, *args, **kwargs):
        share_token = request.GET.get('share')
        if share_token:
            try:
                token = uuid.UUID(str(share_token))
            except (TypeError, ValueError, AttributeError):
                raise Http404
            document = (
                Document.objects.filter(
                    pk=kwargs['pk'],
                    public_link_token=token,
                    is_deleted=False,
                )
                .select_related(
                    'project',
                    'section',
                    'current_version',
                    'current_version__uploaded_by',
                )
                .first()
            )
            if document is None:
                raise Http404
            if (
                document.public_link_expires_at is None
                or document.public_link_expires_at <= timezone.now()
            ):
                return HttpResponseGone('Срок действия ссылки истек')
            self.object = document
            self.public_view = True
            _remember_document(request, document.pk)
            context = self.get_context_data(object=document)
            context['public_view'] = True
            return self.render_to_response(context)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return get_accessible_documents(self.request.user).select_related(
            'project',
            'section',
            'current_version',
            'current_version__uploaded_by',
        )

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not check_read_permission(request.user, self.object):
            return HttpResponseForbidden('Нет прав на просмотр документа')
        _remember_document(request, self.object.pk)
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def _resolve_selected_version(self):
        """Версия для просмотра с учётом ?version=<id>."""
        document = self.object
        if getattr(self, 'public_view', False):
            return document.current_version, True

        if 'version' not in self.request.GET:
            return document.current_version, True

        raw_version = self.request.GET.get('version', '')
        try:
            version_id = int(raw_version)
        except (TypeError, ValueError):
            raise Http404

        try:
            version = get_document_version_for_view(
                self.request.user, document, version_id
            )
        except (DocumentVersion.DoesNotExist, PermissionDenied, ValueError):
            raise Http404
        return version, version.pk == document.current_version_id

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_version, is_current_version = self._resolve_selected_version()

        if getattr(self, 'public_view', False):
            versions = self.object.versions.select_related('uploaded_by').order_by(
                '-version_number'
            )
        else:
            versions = get_document_versions(self.request.user, self.object)

        if selected_version is not None:
            pdf_url = reverse(
                'storage:document_download', args=[selected_version.pk]
            )
        else:
            pdf_url = ''

        if is_current_version or self.object.current_version_id is None:
            pdf_url_current = ''
        else:
            pdf_url_current = reverse(
                'storage:document_download',
                args=[self.object.current_version_id],
            )

        share_query = ''
        if getattr(self, 'public_view', False):
            share_token = self.request.GET.get('share')
            if share_token:
                share_query = '?' + urlencode({'share': share_token})
        if share_query:
            if pdf_url:
                pdf_url += share_query
            if pdf_url_current:
                pdf_url_current += share_query

        params = self.request.GET.copy()
        params.pop('version', None)
        current_version_url = self.request.path
        if params:
            current_version_url += '?' + params.urlencode()

        context['breadcrumbs'] = _document_breadcrumbs(self.object)
        context['versions'] = versions
        context['selected_version'] = selected_version
        context['is_current_version'] = is_current_version
        context['pdf_url'] = pdf_url
        context['pdf_url_current'] = pdf_url_current
        context['current_version_url'] = current_version_url

        # Спринт 8: сравнение доступно из ЛЮБОЙ версии — цель сравнения
        # выбирается в выпадающем меню (текущая версия по умолчанию).
        pdf_urls = {}
        for version_item in versions:
            url = reverse(
                'storage:document_download', args=[version_item.pk],
            )
            if share_query:
                url += share_query
            pdf_urls[str(version_item.pk)] = url
        context['pdf_urls_json'] = json.dumps(pdf_urls)
        context['selected_version_pk'] = (
            selected_version.pk if selected_version else None
        )
        context['open_issues_count'] = 0
        context['issues_registry_url'] = ''
        issues = list(get_issues_for_version(self.object))

        # Спринт 5: по публичной share-ссылке аноним не должен получать
        # тексты, статусы, авторов и ответы замечаний — отдаём пустые
        # списки и пропускаем ролевые вычисления по каждому замечанию.
        if getattr(self, 'public_view', False):
            context['issues'] = []
            context['issues_json'] = []
            context['issue_assignees'] = []
        else:
            status_labels = dict(Issue.STATUS_CHOICES)
            for issue in issues:
                issue.allowed_status_options = [
                    (code, status_labels[code])
                    for code in allowed_issue_statuses(self.request.user, issue)
                ]
                issue.allowed_status_json = json.dumps(
                    [
                        {'code': code, 'label': label}
                        for code, label in issue.allowed_status_options
                    ],
                    ensure_ascii=False,
                )
            context['issues'] = issues
            payloads = []
            for issue in issues:
                payload = _issue_payload(issue)
                payload['status_options'] = [
                    {'code': code, 'label': label}
                    for code, label in issue.allowed_status_options
                ]
                payloads.append(payload)
            context['issues_json'] = payloads
            context['issue_assignees'] = get_issue_assignees(
                self.request.user, self.object
            )
            context['open_issues_count'] = _open_issues_count(self.object)
            context['issues_registry_url'] = '{}?{}'.format(
                reverse('issues:issue_registry', args=[self.object.project_id]),
                urlencode({'q': self.object.code}),
            )
        context['can_create_issue'] = check_issue_permission(
            self.request.user, self.object
        )
        context['workflow_actions'] = _workflow_actions_payload(
            self.request.user, self.object
        )
        return context


class DocumentUploadAjaxView(LoginRequiredMixin, View):
    def handle_no_permission(self):
        return JsonResponse({'error': 'Требуется авторизация'}, status=403)

    def post(self, request, project_id):
        project = get_accessible_projects(request.user).filter(pk=project_id).first()
        if project is None:
            return JsonResponse({'error': 'Проект не найден'}, status=404)

        section_id = request.POST.get('section_id')
        folder_id = request.POST.get('folder_id')
        file = request.FILES.get('file')
        if file is None:
            return JsonResponse({'error': 'Файл не выбран'}, status=400)

        folder = None
        if folder_id:
            folder = Folder.objects.filter(
                project=project,
                pk=folder_id,
                is_deleted=False,
            ).first()
            if folder is None:
                return JsonResponse({'error': 'Папка не найдена'}, status=400)

        if folder is not None and folder.section_id is not None:
            section = folder.section
            if section_id and str(section.pk) != str(section_id):
                return JsonResponse(
                    {'error': 'Раздел не совпадает с разделом папки'},
                    status=400,
                )
        else:
            if not section_id:
                return JsonResponse({'error': 'Не выбран раздел'}, status=400)
            try:
                section = Section.objects.get(
                    pk=section_id,
                    project__company=project.company,
                )
            except (Section.DoesNotExist, ValueError, TypeError):
                return JsonResponse({'error': 'Раздел не найден'}, status=400)

        if folder is not None and folder.project_id != project.pk:
            return JsonResponse({'error': 'Папка не найдена'}, status=400)

        code = (request.POST.get('code') or file.name).strip()
        name = (request.POST.get('name') or file.name).strip()
        doc_type = request.POST.get('doc_type') or 'other'

        try:
            version = upload_document(
                user=request.user,
                project=project,
                section=section,
                code=code,
                name=name,
                doc_type=doc_type,
                file=file,
                comment=request.POST.get('comment', ''),
                folder=folder,
            )
        except ValidationError as exc:
            return JsonResponse(
                {'error': ' '.join(exc.messages)},
                status=400,
            )
        except (PermissionDenied, ValueError) as exc:
            return JsonResponse({'error': str(exc)}, status=400)

        response = JsonResponse({
            'status': 'ok',
            'version': version.version_number,
        })
        response['X-Document-Id'] = str(version.document_id)
        return response


class DocumentDownloadView(View):
    def get(self, request, version_id):
        share_token = (request.GET.get('share') or '').strip()
        versions = DocumentVersion.objects.select_related('document__project').filter(
            document__is_deleted=False,
        )

        if share_token:
            try:
                token = uuid.UUID(share_token)
            except (TypeError, ValueError, AttributeError):
                raise Http404
            version = versions.filter(
                pk=version_id,
                document__public_link_token=token,
            ).first()
            if version is None:
                raise Http404
            expires_at = version.document.public_link_expires_at
            if expires_at is None or expires_at <= timezone.now():
                return HttpResponseGone('Срок действия ссылки истек')
        else:
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path())
            if not request.user.is_superuser:
                company = get_user_company(request.user)
                if company is None:
                    return HttpResponseForbidden('Нет прав на скачивание файла')
                versions = versions.filter(document__project__company=company)

            version = get_object_or_404(versions, pk=version_id)
            if not check_read_permission(request.user, version.document):
                return HttpResponseForbidden('Нет прав на скачивание файла')

        _remember_document(request, version.document_id)

        if not version.file:
            return HttpResponseNotFound('Файл не найден')

        try:
            file_handle = version.file.open('rb')
        except FileNotFoundError:
            return HttpResponseNotFound('Файл не найден')

        filename = version.file.name.rsplit('/', 1)[-1]
        # Спринт 5: отдаём файл под осмысленным именем из шифра документа;
        # get_valid_filename обезвреживает кавычки/переносы строк из кода.
        extension = filename.rsplit('.', 1)[-1] if '.' in filename else 'pdf'
        download_name = get_valid_filename(
            f'{version.document.code}.{extension}'
        )
        return FileResponse(file_handle, as_attachment=True, filename=download_name)


@login_required
def document_pdf_with_issues(request, document_id):
    document = get_object_or_404(
        get_accessible_documents(request.user).select_related('current_version'),
        pk=document_id,
    )
    try:
        pdf_bytes = export_pdf_with_issues(request.user, document)
    except PermissionDenied as exc:
        return HttpResponseForbidden(str(exc))
    except (ValueError, FileNotFoundError) as exc:
        return HttpResponseNotFound(str(exc))

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="{get_valid_filename(document.code)}.pdf"'
    )
    return response


class ExportVedomostView(LoginRequiredMixin, View):
    def get(self, request, project_id):
        project = get_object_or_404(get_accessible_projects(request.user), pk=project_id)

        documents = get_accessible_documents(request.user).filter(project=project).select_related(
            'section',
            'current_version',
            'current_version__uploaded_by',
        ).order_by('section__order', 'code')

        section_id = request.GET.get('section_id')
        if section_id:
            try:
                documents = documents.filter(section_id=int(section_id))
            except (TypeError, ValueError):
                return HttpResponseBadRequest('Неверный идентификатор раздела')

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Ведомость'

        sheet.append(['Шифр', 'Название', 'Тип', 'Версия', 'Дата', 'Автор'])
        for cell in sheet[1]:
            cell.font = Font(bold=True)

        for row, document in enumerate(documents, start=2):
            version = document.current_version

            sheet.cell(row=row, column=1, value=document.code)

            name_cell = sheet.cell(row=row, column=2, value=document.name)
            if version is not None:
                download_url = request.build_absolute_uri(
                    reverse('storage:document_download', args=[version.pk])
                )
                name_cell.hyperlink = download_url
                name_cell.font = Font(color='0563C1', underline='single')

            sheet.cell(row=row, column=3, value=document.get_doc_type_display())
            sheet.cell(row=row, column=4, value=version.version_number if version else '-')
            sheet.cell(
                row=row,
                column=5,
                value=self._format_datetime(version.uploaded_at if version else None),
            )
            sheet.cell(row=row, column=6, value=self._format_author(version))

        for column, width in {'A': 18, 'B': 40, 'C': 22, 'D': 10, 'E': 18, 'F': 24}.items():
            sheet.column_dimensions[column].width = width

        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        return FileResponse(
            buffer,
            as_attachment=True,
            filename='vedomost.xlsx',
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    @staticmethod
    def _format_datetime(value):
        if value is None:
            return ''
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime('%d.%m.%Y %H:%M')

    @staticmethod
    def _format_author(version):
        if version is None:
            return ''
        return version.uploaded_by.get_full_name() or version.uploaded_by.username


class SimulateMailImportView(LoginRequiredMixin, View):
    def post(self, request, project_id):
        project = get_object_or_404(get_accessible_projects(request.user), pk=project_id)

        rule = MailRule.objects.filter(project=project).order_by('pk').first()
        if rule is None:
            messages.error(request, 'Для проекта не настроены правила импорта из почты.')
            return redirect('storage:project_detail', pk=project.pk)

        filename = 'Исходные_данные_из_почты.pdf'
        fake_file = SimpleUploadedFile(
            filename,
            b'%PDF-1.4 simulated mail attachment',
            content_type='application/pdf',
        )

        try:
            upload_document(
                user=request.user,
                project=project,
                section=rule.section,
                code='ИЗ-ПОЧТЫ-ИД',
                name='Исходные данные из почты',
                doc_type=rule.doc_type,
                file=fake_file,
                comment='Импортировано из почты (симуляция)',
            )
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return redirect('storage:project_detail', pk=project.pk)
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect('storage:project_detail', pk=project.pk)

        messages.success(request, 'Письмо обработано, файл добавлен.')
        return redirect('storage:project_detail', pk=project.pk)


class EditMemberForm(forms.ModelForm):
    email = forms.EmailField(label='Email', required=False)
    projects = forms.ModelMultipleChoiceField(
        queryset=Project.objects.none(),
        required=False,
        label='Проекты',
    )
    allowed_sections = forms.ModelMultipleChoiceField(
        queryset=Section.objects.none(),
        required=False,
        label='Разделы',
    )

    class Meta:
        model = UserProfile
        fields = ('role', 'company', 'phone')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('email', 'phone'):
            self.fields[name].widget.attrs['class'] = 'form-control form-control-sm'
        self.fields['role'].widget.attrs['class'] = 'form-select form-select-sm'
        self.fields['company'].widget.attrs['class'] = 'form-select form-select-sm'
        self.fields['projects'].widget.attrs.update({
            'class': 'form-select form-select-sm',
            'size': '5',
        })
        self.fields['allowed_sections'].widget.attrs.update({
            'class': 'form-select form-select-sm',
            'size': '5',
        })

        company = self.instance.company if self.instance else None
        if self.is_bound:
            selected_company = self.data.get('company')
            if selected_company:
                company = (
                    Company.objects.filter(pk=selected_company).first()
                    or company
                )
        if company is not None:
            self.fields['projects'].queryset = Project.objects.filter(
                company=company
            ).order_by('code')
            self.fields['allowed_sections'].queryset = Section.objects.filter(
                project__company=company
            ).select_related('project').order_by('project__code', 'order')
            if self.instance.user_id:
                self.fields['projects'].initial = list(
                    ProjectMembership.objects.filter(
                        user=self.instance.user,
                        project__company=company,
                    ).values_list('project_id', flat=True)
                )
                self.fields['allowed_sections'].initial = list(
                    self.instance.allowed_sections.values_list('pk', flat=True)
                )

        if self.instance and self.instance.user_id:
            self.fields['email'].initial = self.instance.user.email

    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        user.email = self.cleaned_data.get('email', '')
        user.save()
        if commit:
            profile.save()
            profile.allowed_sections.set(
                self.cleaned_data.get('allowed_sections') or []
            )
            if profile.company_id:
                selected_projects = self.cleaned_data.get('projects') or []
                ProjectMembership.objects.filter(
                    user=user,
                    project__company=profile.company,
                ).exclude(project__in=selected_projects).delete()
                project_role = (
                    profile.role
                    if profile.role in dict(
                        ProjectMembership.ROLE_IN_PROJECT_CHOICES
                    )
                    else 'gip'
                )
                for project in selected_projects:
                    ProjectMembership.objects.update_or_create(
                        user=user,
                        project=project,
                        defaults={'role_in_project': project_role},
                    )
        return profile


class EditMemberView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = UserProfile
    form_class = EditMemberForm
    template_name = 'storage/edit_member.html'
    success_url = reverse_lazy('storage:dashboard')

    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True
        profile = getattr(user, 'userprofile', None)
        return profile is not None and profile.role in ('admin', 'gip')

    def get_queryset(self):
        queryset = UserProfile.objects.select_related('user', 'company')
        actor = self.request.user
        if actor.is_superuser:
            return queryset
        actor_profile = getattr(actor, 'userprofile', None)
        if actor_profile is None:
            return queryset.none()
        queryset = queryset.filter(company=actor_profile.company)
        if actor_profile.role != 'admin':
            queryset = queryset.exclude(role='admin')
        return queryset

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        actor = self.request.user
        actor_profile = getattr(actor, 'userprofile', None)
        if not actor.is_superuser:
            form.fields['company'].queryset = Company.objects.filter(
                pk=actor_profile.company_id
            )
            form.fields['company'].disabled = True
            if actor_profile.role != 'admin':
                form.fields['role'].choices = [
                    choice
                    for choice in form.fields['role'].choices
                    if choice[0] != 'admin'
                ]
        return form

    def form_valid(self, form):
        actor = self.request.user
        if not actor.is_superuser:
            actor_profile = getattr(actor, 'userprofile', None)
            if (
                self.object.role == 'admin'
                or form.cleaned_data.get('role') == 'admin'
            ) and actor_profile.role != 'admin':
                raise PermissionDenied(
                    'Только администратор может изменять администраторов'
                )
            form.instance.company = actor_profile.company
        return super().form_valid(form)


class DeleteMemberView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True
        profile = getattr(user, 'userprofile', None)
        return profile is not None and profile.role == 'admin'

    def post(self, request, pk):
        queryset = UserProfile.objects.select_related('user', 'company')
        if not request.user.is_superuser:
            actor_profile = getattr(request.user, 'userprofile', None)
            queryset = queryset.filter(company=actor_profile.company)
        profile = get_object_or_404(queryset, pk=pk)
        remove_company_member(
            request.user,
            profile.company,
            profile.user,
        )
        messages.success(request, 'Участник удалён из компании.')
        return redirect('storage:dashboard')


class DashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'storage/dashboard.html'

    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True
        profile = getattr(user, 'userprofile', None)
        return profile is not None and profile.role in ('admin', 'gip')

    def get_company(self):
        company = get_user_company(self.request.user)
        if company is None and self.request.user.is_superuser:
            company = Company.objects.first()
        return company

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self.get_company()
        context['company'] = company
        context['members'] = (
            get_company_members(company)
            if company is not None
            else UserProfile.objects.none()
        )
        context['role_choices'] = UserProfile.ROLE_CHOICES
        context['available_users'] = (
            get_user_model()
            .objects.filter(
                Q(userprofile__isnull=True)
                | Q(userprofile__company__isnull=True)
            )
            .order_by('last_name', 'username')
            if company is not None
            else get_user_model().objects.none()
        )
        context['available_sections'] = (
            Section.objects.filter(project__company=company)
            .select_related('project')
            .order_by('project__code', 'order')
            if company is not None
            else Section.objects.none()
        )
        context['all_companies'] = (
            Company.objects.order_by('name')
            if self.request.user.is_superuser
            else Company.objects.none()
        )
        context['projects'] = (
            Project.objects.filter(company=company).order_by('code')
            if company is not None
            else Project.objects.none()
        )
        return context

    def post(self, request):
        company = self.get_company()
        if company is None:
            messages.error(request, 'Компания не найдена.')
            return redirect('storage:dashboard')

        action = request.POST.get('action')
        try:
            if action == 'update_company':
                update_company_info(
                    company,
                    name=request.POST.get('name', '').strip(),
                    logo=request.FILES.get('logo'),
                    is_private=request.POST.get('is_private') == 'on',
                )
                messages.success(request, 'Информация о компании обновлена.')
            elif action == 'add_member':
                username = request.POST.get('username', '').strip()
                user = None
                if username:
                    user = get_user_model().objects.filter(
                        username=username
                    ).first()
                    if user is None:
                        password = request.POST.get('password', '')
                        if not password:
                            raise ValueError(
                                'Для нового пользователя укажите пароль'
                            )
                        # Спринт 5: create_user обходит AUTH_PASSWORD_VALIDATORS —
                        # валидируем вручную, чтобы «123» не прошёл.
                        from django.contrib.auth.password_validation import (
                            validate_password,
                        )
                        from django.core.exceptions import ValidationError as _ValidationError

                        try:
                            validate_password(password)
                        except _ValidationError as exc:
                            raise ValueError('; '.join(exc.messages))
                        user = get_user_model().objects.create_user(
                            username=username,
                            password=password,
                        )
                else:
                    user = get_user_model().objects.filter(
                        pk=request.POST.get('user_id')
                    ).first()

                if user is None:
                    raise ValueError('Пользователь не найден')

                selected_company = company
                company_id = request.POST.get('company_id')
                if request.user.is_superuser and company_id:
                    selected_company = (
                        Company.objects.filter(pk=company_id).first()
                        or company
                    )

                allowed_sections = [
                    int(pk)
                    for pk in request.POST.getlist('allowed_sections')
                    if str(pk).isdigit()
                ]
                role = request.POST.get('role', '')
                profile = add_company_member(
                    request.user,
                    selected_company,
                    user,
                    role,
                    allowed_sections,
                )

                profile.phone = request.POST.get('phone', '').strip()
                profile.save(update_fields=['phone'])

                full_name = request.POST.get('full_name', '').strip()
                if full_name:
                    parts = full_name.split()
                    user.first_name = parts[0]
                    user.last_name = ' '.join(parts[1:])
                email = request.POST.get('email', '').strip()
                if email:
                    user.email = email
                user.save()

                project_id = request.POST.get('project_id')
                if project_id:
                    project = Project.objects.filter(
                        pk=project_id,
                        company=selected_company,
                    ).first()
                    if project is not None:
                        project_role = (
                            role
                            if role in dict(
                                ProjectMembership.ROLE_IN_PROJECT_CHOICES
                            )
                            else 'gip'
                        )
                        ProjectMembership.objects.update_or_create(
                            project=project,
                            user=user,
                            defaults={'role_in_project': project_role},
                        )

                messages.success(request, 'Участник добавлен.')
            elif action == 'remove_member':
                user = get_user_model().objects.filter(
                    pk=request.POST.get('user_id')
                ).first()
                if user is not None:
                    remove_company_member(request.user, company, user)
                    messages.success(request, 'Участник удалён из компании.')
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))

        return redirect('storage:dashboard')


FEEDBACK_STATUS_BADGE = {
    "NEW": "cde-badge--new",
    "IN_PROGRESS": "cde-badge--in_progress",
    "RESOLVED": "cde-badge--done",
    "REJECTED": "cde-badge--rejected",
}

FEEDBACK_CATEGORY_BADGE = {
    "BUG": "cde-badge--attention",
    "UX": "cde-badge--review",
    "IDEA": "cde-badge--wait",
    "OTHER": "cde-badge--closed",
}


class SubmitFeedbackView(LoginRequiredMixin, View):
    """Принимает обратную связь из модалки через fetch/POST."""

    def post(self, request):
        company = get_user_company(request.user)
        if company is None:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "non_field_errors": [
                            "У пользователя не указана компания"
                        ]
                    },
                },
                status=400,
            )

        try:
            feedback = submit_feedback(
                request.user,
                company,
                request.POST.get("message", ""),
                category=request.POST.get("category") or Feedback.Category.BUG,
                page_url=request.POST.get("page_url", ""),
                contact_email=request.POST.get("contact_email", ""),
                screenshot=request.FILES.get("screenshot"),
                file_storage=LocalFileStorage(),
            )
        except PermissionDenied as exc:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {"non_field_errors": [str(exc)]},
                },
                status=403,
            )
        except ValidationError as exc:
            if getattr(exc, "error_dict", None):
                errors = exc.message_dict
            else:
                errors = {"non_field_errors": exc.messages}
            return JsonResponse({"ok": False, "errors": errors}, status=400)

        return JsonResponse({"ok": True, "id": feedback.pk})


class FeedbackListView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Список обратной связи компании для администратора."""

    raise_exception = True
    paginate_by = 50

    def test_func(self):
        user = self.request.user
        return user.is_superuser or getattr(
            getattr(user, "userprofile", None), "role", None
        ) == "admin"

    def get(self, request):
        is_superuser = request.user.is_superuser
        companies = None

        if is_superuser:
            companies = Company.objects.order_by("name")
            company = None
            raw_company = (request.GET.get("company") or "").strip()
            if raw_company:
                try:
                    company = companies.filter(pk=int(raw_company)).first()
                except (TypeError, ValueError):
                    company = None
                if company is None:
                    return HttpResponseBadRequest("Компания не найдена")
            if company is None:
                company = get_user_company(request.user) or companies.first()
        else:
            company = get_user_company(request.user)

        if company is None:
            return HttpResponseBadRequest("Не указана компания")

        status = (request.GET.get("status") or "").strip() or None
        category = (request.GET.get("category") or "").strip() or None

        try:
            queryset = list_feedback(
                request.user,
                company,
                status=status,
                category=category,
            )
        except ValidationError as exc:
            return HttpResponseBadRequest(str(exc))

        paginator = Paginator(queryset, self.paginate_by)
        page_obj = paginator.get_page(request.GET.get("page"))

        feedback_items = list(page_obj.object_list)
        for item in feedback_items:
            item.status_badge_class = FEEDBACK_STATUS_BADGE.get(
                item.status, "cde-badge--closed"
            )
            item.category_badge_class = FEEDBACK_CATEGORY_BADGE.get(
                item.category, "cde-badge--closed"
            )

        return render(
            request,
            "feedback_list.html",
            {
                "page_obj": page_obj,
                "feedback_items": feedback_items,
                "status_choices": Feedback.Status.choices,
                "category_choices": Feedback.Category.choices,
                "current_status": status or "",
                "current_category": category or "",
                "companies": companies,
                "selected_company_id": company.pk,
            },
        )


class FeedbackStatusUpdateView(LoginRequiredMixin, View):
    """AJAX-смена статуса/комментария обратной связи."""

    def post(self, request, feedback_id):
        feedback = get_object_or_404(Feedback, pk=feedback_id)
        try:
            updated = update_feedback_status(
                request.user,
                feedback,
                request.POST.get("status") or feedback.status,
                request.POST.get("admin_comment", ""),
            )
        except PermissionDenied as exc:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {"non_field_errors": [str(exc)]},
                },
                status=403,
            )
        except ValidationError as exc:
            if getattr(exc, "error_dict", None):
                errors = exc.message_dict
            else:
                errors = {"non_field_errors": exc.messages}
            return JsonResponse({"ok": False, "errors": errors}, status=400)

        return JsonResponse(
            {
                "ok": True,
                "id": updated.pk,
                "status": updated.status,
                "status_display": updated.get_status_display(),
                "status_class": FEEDBACK_STATUS_BADGE.get(
                    updated.status, "cde-badge--closed"
                ),
            }
        )
