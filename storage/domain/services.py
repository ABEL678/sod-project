import base64
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import io
import logging

from django.conf import settings
import uuid

from PIL import Image, UnidentifiedImageError

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.validators import validate_email
from django.db.models import Count, Max, Prefetch, Q, QuerySet
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

from storage.domain.models import (
    Company,
    DesignTask,
    DesignTaskAttachment,
    DesignTaskChecklistItem,
    DesignTaskStatusLog,
    Document,
    DocumentVersion,
    DocumentView,
    Feedback,
    Folder,
    MailRule,
    Project,
    ProjectMembership,
    Section,
    Specification,
    TaskTemplate,
    User,
    UserProfile,
    VorItem,
)
from issues.models import Issue, IssueComment
from issues.notify import (
    create_notification,
    notify_issue_assigned,
    notify_issue_commented,
    notify_issue_status_changed,
)
from storage.domain.design_colors import ISSUE_STATUS_RGB as PDF_ISSUE_COLORS
from storage.domain.project_templates import (
    IRD_FOLDERS,
    PD_SECTIONS,
    PROJECT_TEMPLATES,
    RD_SECTIONS,
    ROOT_SUPERFOLDERS,
    ROOT_SUPERFOLDER_NAMES,
)


DOCUMENT_VIEW_COLUMNS = (
    'code', 'section', 'folder', 'name', 'doc_type', 'zone', 'suitability',
    'internal_status', 'issues', 'version', 'date', 'author',
)
DOCUMENT_VIEW_FILTERS = (
    'section', 'doc_type', 'status', 'suitability_code', 'internal_status',
    'code', 'name',
)
DOCUMENT_VIEW_SORT_FIELDS = ('code', 'name', 'date', 'version')
DOCUMENT_VIEW_MIN_WIDTH = 50
DOCUMENT_VIEW_MAX_WIDTH = 800

ZONE_STATUS_MAP = {
    'WIP': 'draft',
    'SHARED': 'in_review',
    'PUBLISHED': 'approved',
    'ARCHIVE': 'archived',
}

ZONE_SUITABILITY_MAP = {
    'WIP': 'S0',
    'SHARED': 'S3',
    'PUBLISHED': 'A',
    'ARCHIVE': 'A',
}

# Спринт 5: валидация загружаемых файлов — белый список расширений и лимит размера.
# Подобран для СЭД: чертежи/CAD, документы Office, изображения, архивы, письма.
ALLOWED_UPLOAD_EXTENSIONS = frozenset({
    'pdf', 'dwg', 'dxf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'png', 'jpg', 'jpeg', 'tif', 'tiff', 'zip', 'rar', '7z',
    'rvt', 'rfa', 'ifc', 'nwd', 'nwc', 'txt', 'csv', 'msg', 'eml',
})
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 МБ — согласовано с nginx client_max_body_size


def _status_for_zone(folder: Folder | None) -> str | None:
    if not _is_canonical_zone_folder(folder):
        return None
    return ZONE_STATUS_MAP.get(folder.zone)


def _suitability_for_zone(folder: Folder | None) -> str | None:
    if not _is_canonical_zone_folder(folder):
        return None
    return ZONE_SUITABILITY_MAP.get(folder.zone)


def _approval_target_changes(
    document: Document,
    target_status: str | None,
    target_suitability: str | None,
    target_zone: str | None,
) -> bool:
    """True, если операция фактически переводит документ в approved/A."""
    status_changed = (
        target_status in ('approved', 'S4')
        and target_status != document.status
    )
    suitability_changed = (
        target_suitability in ('A', 'S4')
        and target_suitability != document.suitability_code
        and target_zone == 'PUBLISHED'
    )
    return status_changed or suitability_changed


def _is_canonical_zone_folder(folder: Folder | None) -> bool:
    """Проверяет, является ли папка системной папкой зоны проекта."""
    if folder is None:
        return False
    return bool(
        folder.is_system_zone
        and folder.parent_id is None
        and folder.section_id is None
        and folder.zone in dict(Folder.ZONE_CHOICES)
    )


def _document_row(document: Document) -> dict:
    """Единый формат строки документа для ведомости и видов таблицы."""
    version = document.current_version
    author = ''
    if version is not None:
        author = version.uploaded_by.get_full_name() or version.uploaded_by.username

    zone_code = ''
    zone_name = ''
    if document.folder:
        zone_code = document.folder.zone
        zone_name = document.folder.get_zone_display()

    return {
        'id': document.pk,
        'code': document.code,
        'name': document.name,
        'doc_type': document.get_doc_type_display(),
        'doc_type_code': document.doc_type,
        'status': document.get_status_display(),
        'status_code': document.status,
        'zone_code': zone_code,
        'zone_name': zone_name,
        'version': version.version_number if version else '-',
        'version_id': document.current_version_id,
        'date': version.uploaded_at if version else '',
        'author': author,
        'section_id': document.section_id,
    }


def get_user_company(user: User) -> Company | None:
    """Возвращает компанию пользователя из профиля."""
    profile = getattr(user, 'userprofile', None)
    return profile.company if profile is not None else None


def get_my_company(user: User) -> Company | None:
    """Компания текущего пользователя для UI (хедер, Панель управления).

    Алиас get_user_company: пользователь может быть без профиля или без
    компании — вернётся None, UI покажет блок «Моя компания».
    """
    return get_user_company(user)


def attach_user_to_company(
    user: User,
    company: Company,
    role: str = 'admin',
    actor: User | None = None,
) -> UserProfile:
    """Создаёт/обновляет UserProfile и привязывает пользователя к компании.

    Права (actor): только superuser или администратор ЭТОЙ компании.
    По умолчанию actor = user — самопривязка в Панели управления.
    Роль должна быть из UserProfile.ROLE_CHOICES.
    """
    if company is None:
        raise ValueError('Компания не указана')
    if role not in dict(UserProfile.ROLE_CHOICES):
        raise ValueError('Неизвестная роль пользователя')

    actor = actor if actor is not None else user
    _clear_profile_cache(actor)
    actor_profile = getattr(actor, 'userprofile', None)
    allowed = actor.is_superuser or (
        actor_profile is not None
        and actor_profile.role == 'admin'
        and actor_profile.company_id == company.pk
    )
    if not allowed:
        raise PermissionDenied(
            'Привязывать к компании может только администратор компании '
            'или superuser'
        )

    with transaction.atomic():
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.company = company
        profile.role = role
        profile.save()
    _clear_profile_cache(user)
    return profile


def _clear_profile_cache(user: User) -> None:
    """Сбрасывает кэш обратной связи user.userprofile.

    Любой запрос ``UserProfile.objects.filter(user=...)`` через механизм
    known_related_objects кэширует профиль на инстансе пользователя.
    После update_or_create/save кэш устаревает, и проверки ролей
    (_is_company_admin, get_user_company, can_transition) начинают видеть
    прежние значения роли/компании — например, обновлённый в Панели
    управления админ ещё воспринимался как viewer до конца запроса.
    """
    cache = getattr(getattr(user, '_state', None), 'fields_cache', None)
    if cache is not None:
        cache.pop('userprofile', None)


def _check_company_access(user: User, company_id: int) -> bool:
    """Проверяет, что пользователь принадлежит компании (superuser — вне ограничений)."""
    if user.is_superuser:
        return True
    # Спринт 8: кэш на время жизни инстанса пользователя (один запрос).
    # Без него каждая строка реестра × каждое действие дёргало БД
    # (get_user_company = 2 запроса) — на 25 строках ~140 запросов.
    if not hasattr(user, '_sod_company_cache'):
        user._sod_company_cache = get_user_company(user)
    company = user._sod_company_cache
    return company is not None and company.pk == company_id


def get_accessible_projects(user: User):
    """Проекты компании пользователя (superuser видит всё, кроме удалённых)."""
    queryset = Project.objects.filter(is_deleted=False)
    if user.is_superuser:
        return queryset
    company = get_user_company(user)
    if company is None:
        return queryset.none()
    return queryset.filter(company=company)


def get_deleted_projects(user: User):
    """Проекты компании в корзине (только для просмотра/восстановления)."""
    queryset = Project.objects.filter(is_deleted=True)
    if user.is_superuser:
        return queryset
    company = get_user_company(user)
    if company is None:
        return queryset.none()
    return queryset.filter(company=company)


def get_accessible_documents(user: User):
    """Документы, доступные пользователю с учётом компании и роли.

    Contractor видит только разделы из allowed_sections. Остальные роли
    ограничены компанией; superuser видит всё.
    """
    queryset = Document.objects.filter(is_deleted=False)
    if user.is_superuser:
        return queryset
    company = get_user_company(user)
    if company is None:
        return queryset.none()
    queryset = queryset.filter(project__company=company)

    profile = getattr(user, 'userprofile', None)
    if profile is not None and profile.role == 'contractor':
        queryset = queryset.filter(section__in=profile.allowed_sections.all())

    return queryset


def get_accessible_sections(user: User, project: Project):
    """Разделы проекта, доступные пользователю с учётом компании и роли."""
    queryset = Section.objects.filter(
        project=project,
        project__in=get_accessible_projects(user),
    )

    if not user.is_superuser:
        profile = getattr(user, 'userprofile', None)
        if profile is not None and profile.role == 'contractor':
            queryset = queryset.filter(pk__in=profile.allowed_sections.values('pk'))

    return queryset.order_by('order', 'code')


def normalize_document_view_config(config: dict) -> dict:
    """Проверяет и нормализует настройки вида таблицы документов."""
    if not isinstance(config, dict):
        raise ValueError("Настройки вида должны быть словарём")

    unknown_keys = set(config) - {'columns', 'filters', 'sort', 'widths'}
    if unknown_keys:
        raise ValueError(f"Неизвестные ключи настроек: {', '.join(sorted(unknown_keys))}")

    raw_columns = config.get('columns')
    if raw_columns is None:
        columns = list(DOCUMENT_VIEW_COLUMNS)
    else:
        if isinstance(raw_columns, str):
            raw_columns = [raw_columns]
        if not isinstance(raw_columns, (list, tuple)):
            raise ValueError("Столбцы должны быть списком")

        columns = []
        for column in raw_columns:
            key = str(column).strip()
            if key not in DOCUMENT_VIEW_COLUMNS:
                raise ValueError(f"Неизвестный столбец: {key}")
            if key not in columns:
                columns.append(key)

        if not columns:
            raise ValueError("Нужно выбрать хотя бы один столбец")

    raw_filters = config.get('filters') or {}
    if not isinstance(raw_filters, dict):
        raise ValueError("Фильтры должны быть словарём")

    filters = {}
    for key, value in raw_filters.items():
        if key not in DOCUMENT_VIEW_FILTERS:
            raise ValueError(f"Неизвестный фильтр: {key}")
        if value in (None, '', []):
            continue

        if key == 'section':
            try:
                filters['section'] = int(value)
            except (TypeError, ValueError):
                raise ValueError("Неверный идентификатор раздела")
        elif key == 'doc_type':
            if not isinstance(value, str) or value not in dict(Document.DOC_TYPE_CHOICES):
                raise ValueError("Неверный тип документа")
            filters[key] = value
        elif key == 'status':
            if not isinstance(value, str) or value not in dict(Document.STATUS_CHOICES):
                raise ValueError("Неверный статус документа")
            filters[key] = value
        else:
            if not isinstance(value, str):
                raise ValueError(f"Неверное значение фильтра: {key}")
            value = value.strip()
            if value:
                filters[key] = value

    raw_sort = config.get('sort')
    if raw_sort in (None, ''):
        sort = []
    elif isinstance(raw_sort, str):
        sort = [raw_sort]
    elif isinstance(raw_sort, (list, tuple)):
        sort = list(raw_sort)
    else:
        raise ValueError("Сортировка должна быть списком")

    normalized_sort = []
    for item in sort:
        field = str(item).strip()
        if not field:
            continue
        if field.lstrip('-') not in DOCUMENT_VIEW_SORT_FIELDS:
            raise ValueError(f"Неизвестное поле сортировки: {field}")
        if field not in normalized_sort:
            normalized_sort.append(field)

    raw_widths = config.get('widths')
    if raw_widths is None:
        raw_widths = {}
    if not isinstance(raw_widths, dict):
        raise ValueError("Ширины столбцов должны быть словарём")

    widths = {}
    for key, value in raw_widths.items():
        if key not in DOCUMENT_VIEW_COLUMNS:
            raise ValueError(f"Неизвестный столбец ширины: {key}")
        if isinstance(value, bool):
            raise ValueError("Ширина столбца должна быть целым числом")
        try:
            width = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Ширина столбца должна быть целым числом")
        if isinstance(value, float) and value != width:
            raise ValueError("Ширина столбца должна быть целым числом")
        if width < DOCUMENT_VIEW_MIN_WIDTH or width > DOCUMENT_VIEW_MAX_WIDTH:
            raise ValueError(
                "Ширина столбца должна быть от "
                f"{DOCUMENT_VIEW_MIN_WIDTH} до {DOCUMENT_VIEW_MAX_WIDTH} px"
            )
        widths[key] = width

    return {
        'columns': columns,
        'filters': filters,
        'sort': normalized_sort,
        'widths': widths,
    }


def _ensure_project_access(user: User, project: Project) -> None:
    if not get_accessible_projects(user).filter(pk=project.pk).exists():
        raise PermissionDenied("Нет доступа к проекту")


def can_manage_project_participants(user: User, project: Project) -> bool:
    """Может ли пользователь просматривать и менять участников проекта."""
    if user.is_superuser:
        return True

    profile = getattr(user, 'userprofile', None)
    if profile is None or not _check_company_access(user, project.company_id):
        return False

    # Contractor — только чтение, даже если он ошибочно назначен ГИПом.
    if profile.role == 'contractor':
        return False

    if profile.role == 'admin':
        return True

    if project.gip_id == user.id:
        return True

    return project.memberships.filter(user=user, role_in_project='gip').exists()


def list_project_participants(actor: User, project: Project):
    """Участники проекта для карточки. Доступно только ГИПу или админу."""
    if not can_manage_project_participants(actor, project):
        raise PermissionDenied("Нет прав на просмотр участников проекта")

    return project.memberships.select_related('user').order_by(
        'role_in_project',
        'user__username',
    )


def get_project_membership(actor: User, project: Project, membership_id):
    """Участник проекта по идентификатору с проверкой прав."""
    if not can_manage_project_participants(actor, project):
        raise PermissionDenied("Нет прав на изменение участников проекта")

    try:
        return project.memberships.select_related('user').get(pk=membership_id)
    except (ProjectMembership.DoesNotExist, TypeError, ValueError):
        return None


def save_document_view(
    user: User,
    project: Project,
    name: str,
    config: dict,
    is_shared: bool = False,
) -> DocumentView:
    """Сохраняет личный или общий вид таблицы документов.

    Повторное сохранение вида с тем же именем обновляет его настройки.
    Общие виды может создавать только ГИП проекта или администратор.
    """
    _ensure_project_access(user, project)

    name = (name or '').strip()
    if not name:
        raise ValueError("Укажите название вида")
    if len(name) > 100:
        raise ValueError("Название вида не должно превышать 100 символов")

    if is_shared and not can_manage_project_participants(user, project):
        raise PermissionDenied("Только ГИП или администратор может создавать общие виды")

    normalized_config = normalize_document_view_config(config)
    owner = None if is_shared else user

    with transaction.atomic():
        view, created = DocumentView.objects.get_or_create(
            project=project,
            user=owner,
            name=name,
            defaults={
                'config': normalized_config,
                'is_shared': bool(is_shared),
            },
        )
        if not created:
            view.config = normalized_config
            view.is_shared = bool(is_shared)
            view.save(update_fields=['config', 'is_shared'])

    return view


def list_document_views(user: User, project: Project) -> list[DocumentView]:
    """Личные виды пользователя и общие виды проекта."""
    _ensure_project_access(user, project)

    return list(
        DocumentView.objects.filter(project=project)
        .filter(Q(user=user) | Q(is_shared=True))
        .order_by('-is_shared', 'name')
    )


def get_document_view(user: User, project: Project, view_id) -> DocumentView | None:
    """Возвращает доступный пользователю вид по идентификатору."""
    _ensure_project_access(user, project)

    try:
        view_id = int(view_id)
    except (TypeError, ValueError):
        return None

    return (
        DocumentView.objects.filter(project=project, pk=view_id)
        .filter(Q(user=user) | Q(is_shared=True))
        .first()
    )


def get_sections_with_counts(user: User, project: Project):
    """Разделы проекта с количеством документов в каждом."""
    return get_accessible_sections(user, project).exclude(
        code='COMMON'
    ).annotate(
        document_count=Count(
            'documents',
            filter=Q(documents__is_deleted=False),
            distinct=True,
        ),
    )


def get_project_documents_queryset(
    user: User,
    project: Project,
    config: dict | None = None,
    folder: Folder | None = None,
    zone: str | None = None,
) -> QuerySet:
    """Возвращает queryset документов проекта с учётом сохранённого вида.

    ``folder`` ограничивает выборку документами конкретной папки: навигация
    по дереву папок не должна подмешивать документы соседних папок.
    ``zone`` — сегмент-контрол зон ISO 19650 (Волна 3).
    """
    _ensure_project_access(user, project)

    normalized_config = normalize_document_view_config(config or {})
    queryset = get_accessible_documents(user).filter(project=project)

    if zone in dict(Folder.ZONE_CHOICES):
        queryset = queryset.filter(_zone_filter_q(zone))

    if folder is not None:
        if folder.project_id != project.pk:
            raise ValueError("Папка относится к другому проекту")
        if folder.is_deleted:
            raise ValueError("Папка находится в корзине")
        queryset = queryset.filter(folder=folder)

    filters = normalized_config['filters']
    if 'section' in filters:
        queryset = queryset.filter(section_id=filters['section'])
    if 'doc_type' in filters:
        queryset = queryset.filter(doc_type=filters['doc_type'])
    if 'status' in filters:
        queryset = queryset.filter(status=filters['status'])
    if 'suitability_code' in filters:
        queryset = queryset.filter(suitability_code=filters['suitability_code'])
    if 'internal_status' in filters:
        queryset = queryset.filter(internal_status=filters['internal_status'])
    if 'code' in filters:
        queryset = queryset.filter(code__icontains=filters['code'])
    if 'name' in filters:
        queryset = queryset.filter(name__icontains=filters['name'])

    sort_mapping = {
        'code': 'code',
        'name': 'name',
        'date': 'current_version__uploaded_at',
        'version': 'current_version__version_number',
    }
    ordering = []
    for field in normalized_config['sort']:
        prefix = '-' if field.startswith('-') else ''
        ordering.append(f"{prefix}{sort_mapping[field.lstrip('-')]}")
    if not ordering:
        ordering = ['section__order', 'code']

    queryset = queryset.annotate(
        open_issues_count=Count(
            'issues',
            filter=Q(issues__status__in=OPEN_ISSUE_STATUSES),
            distinct=True,
        ),
        issues_total=Count('issues', distinct=True),
        issues_closed=Count(
            'issues',
            filter=Q(issues__status__in=('CLOSED', 'CANCELLED')),
            distinct=True,
        ),
    )

    return queryset.select_related(
        'section',
        'section__project',
        'project',
        'folder',
        'folder__parent',
        'folder__parent__parent',
        'folder__parent__parent__parent',
        'folder__parent__parent__parent__parent',
        'folder__parent__parent__parent__parent__parent',
        'folder__section',
        'folder__section__project',
        'current_version',
        'current_version__uploaded_by',
        'created_by',
    ).order_by(*ordering)


def get_project_documents_for_view(
    user: User,
    project: Project,
    config: dict | None = None,
    folder: Folder | None = None,
    zone: str | None = None,
) -> list[dict]:
    """Документы проекта для таблицы с учётом сохранённого вида и папки."""
    documents = get_project_documents_queryset(
        user, project, config, folder=folder, zone=zone
    )
    return [_document_row(document) for document in documents]


def update_project_member_role(
    actor: User,
    membership: ProjectMembership,
    role: str,
) -> ProjectMembership:
    """Меняет роль участника проекта. Доступно только ГИПу или админу."""
    if not can_manage_project_participants(actor, membership.project):
        raise PermissionDenied("Нет прав на изменение участников проекта")

    if role not in dict(ProjectMembership.ROLE_IN_PROJECT_CHOICES):
        raise ValueError("Неверная роль участника")

    if role == 'gip':
        _validate_gip_role(membership.user)

    if membership.role_in_project == 'gip' and role != 'gip':
        other_gips = membership.project.memberships.filter(
            role_in_project='gip',
        ).exclude(pk=membership.pk)
        if not other_gips.exists() and membership.project.gip_id is None:
            raise ValueError("Нельзя снять роль последнего ГИП проекта")

    membership.role_in_project = role
    membership.save(update_fields=['role_in_project'])
    return membership


def _validate_gip_role(user: User | None) -> None:
    """ГИПом проекта может быть только ГИП, админ или superuser."""
    if user is None or user.is_superuser:
        return
    profile = getattr(user, 'userprofile', None)
    if profile is None or profile.role not in ('gip', 'admin'):
        raise ValueError("Назначаемый ГИП должен иметь роль ГИП или администратора")


def _ensure_root_superfolders(project: Project, actor: User) -> dict[str, Folder]:
    """Создаёт базовые корневые папки: ИРД, ПД, РД."""
    superfolders = {}
    for index, data in enumerate(ROOT_SUPERFOLDERS, start=1):
        folder, _ = Folder.objects.get_or_create(
            project=project,
            parent=None,
            section=None,
            name=data['name'],
            defaults={
                'code': data['code'],
                'full_code': generate_gost_code(
                    project.base_code,
                    data['code'],
                ),
                'order': index,
                'zone': 'WIP',
                'created_by': actor,
            },
        )
        superfolders[data['key']] = folder
    return superfolders


def _code_prefix(project: Project) -> str:
    base_code = (project.base_code or '').strip()
    return f'{base_code}-' if base_code else ''


def _get_or_create_common_section(project: Project) -> Section:
    """Единый скрытый раздел для документов ПД/РД/ИРД."""
    section, _ = Section.objects.get_or_create(
        project=project,
        code='COMMON',
        defaults={
            'name': 'Общие документы',
            'order': 0,
        },
    )
    return section


def _create_ird_structure(
    project: Project,
    actor: User,
    superfolders: dict[str, Folder],
) -> None:
    """Создаёт группу изысканий и прочие исходные данные."""
    root = superfolders['IRD']
    common = _get_or_create_common_section(project)

    for item in IRD_FOLDERS:
        group = create_folder(
            actor,
            project,
            name=item['name'],
            section=common,
            parent=root,
            code='',
            force=True,
        )
        for child in item.get('children', ()):
            create_folder(
                actor,
                project,
                name=child['name'],
                section=common,
                parent=group,
                code='',
                force=True,
            )


def _create_pd_structure(
    project: Project,
    actor: User,
    superfolders: dict[str, Folder],
) -> None:
    """Создаёт разделы ПД и папки «Часть 1» по ПП №87."""
    root = superfolders['PD']
    prefix = _code_prefix(project)
    common = _get_or_create_common_section(project)

    for section_data in PD_SECTIONS:
        section_folder = create_folder(
            actor,
            project,
            name=section_data['name'],
            section=common,
            parent=root,
            code='',
            force=True,
        )

        subsections = section_data.get('subsections', ())
        if subsections:
            for subsection in subsections:
                sub_folder = create_folder(
                    actor,
                    project,
                    name=subsection['name'],
                    section=common,
                    parent=section_folder,
                    code='',
                    force=True,
                )
                part_code = (
                    f'{prefix}ИОС1.'
                    f"{subsection['number'].split('.')[-1]}"
                )
                create_folder(
                    actor,
                    project,
                    name=subsection['part'],
                    section=common,
                    parent=sub_folder,
                    code=part_code,
                    force=True,
                )
        else:
            part_code = f"{prefix}{section_data['mark']}1"
            create_folder(
                actor,
                project,
                name=section_data['part'],
                section=common,
                parent=section_folder,
                code=part_code,
                force=True,
            )


def _create_rd_structure(
    project: Project,
    actor: User,
    superfolders: dict[str, Folder],
) -> None:
    """Создаёт марки РД и папки «Комплект 1» по ГОСТ Р 21.101-2020."""
    root = superfolders['RD']
    prefix = _code_prefix(project)
    common = _get_or_create_common_section(project)

    for section_data in RD_SECTIONS:
        mark = section_data['mark']
        mark_folder = create_folder(
            actor,
            project,
            name=section_data['name'],
            section=common,
            parent=root,
            code='',
            force=True,
        )
        create_folder(
            actor,
            project,
            name=section_data['name'],
            section=common,
            parent=mark_folder,
            code=f'{prefix}{mark}1',
            force=True,
        )


def _populate_project_template(
    project: Project,
    template: dict,
    actor: User,
) -> None:
    """Создаёт только базовый верхний каркас выбранного шаблона."""
    superfolders = _ensure_root_superfolders(project, actor)
    _create_ird_structure(project, actor, superfolders)
    if template.get('pd'):
        _create_pd_structure(project, actor, superfolders)
    if template.get('rd'):
        _create_rd_structure(project, actor, superfolders)


@transaction.atomic
def reset_project_structure(actor: User, project: Project) -> dict:
    """Удаляет папки проекта и заново разворачивает базовый шаблон."""
    if not can_edit_project(actor, project):
        raise PermissionDenied("Нет прав на пересоздание структуры проекта")
    if project.is_deleted:
        raise ValueError("Проект находится в корзине")

    if Document.objects.filter(project=project, is_deleted=False).exists():
        raise ValidationError(
            "Нельзя пересоздать структуру, пока в проекте есть загруженные "
            "документы. Удалите их сначала."
        )

    template_key = project.template_key or 'FULL'
    template = PROJECT_TEMPLATES.get(template_key)
    if template is None or template_key == 'residential':
        # Легаси-проекты без сохранённого ключа получают полный каркас.
        template_key = 'FULL'
        template = PROJECT_TEMPLATES['FULL']

    roots = Folder.objects.filter(
        project=project,
        parent=None,
        section=None,
    ).exclude(is_system_zone=True)
    removed_folders = roots.delete()[0]
    # Удаляем и старые технические Section: активных документов уже нет.
    removed_sections = Section.objects.filter(project=project).delete()[0]

    _ensure_project_zones(project, actor)
    before_folders = Folder.objects.filter(project=project).count()
    before_sections = Section.objects.filter(project=project).count()

    _populate_project_template(project, template, actor)
    if project.template_key != template_key:
        project.template_key = template_key
        project.save(update_fields=['template_key'])

    return {
        'removed_folders': removed_folders,
        'removed_sections': removed_sections,
        'created_folders': (
            Folder.objects.filter(project=project).count() - before_folders
        ),
        'created_sections': (
            Section.objects.filter(project=project).count() - before_sections
        ),
        'template_key': template_key,
    }


def _ensure_project_zones(project: Project, user: User | None = None) -> bool:
    """Создаёт зоны для проекта, если не все 4 канонические зоны существуют.

    Проверяет наличие всех 4 канонических зон (WIP, SHARED, PUBLISHED, ARCHIVE).
    Создаёт только отсутствующие. Идемпотентно — не создаёт дубликаты.

    Возвращает True, если зоны были созданы (проект был без зон).
    """
    existing_zones = set(
        Folder.objects.filter(
            project=project,
            zone__in=dict(Folder.ZONE_CHOICES),
            is_system_zone=True,
            code='',
            parent__isnull=True,
            section__isnull=True,
        ).values_list('zone', flat=True)
    )
    required_zones = {code for code, _ in Folder.ZONE_CHOICES}
    missing_zones = required_zones - existing_zones
    if not missing_zones:
        return False

    zone_name_map = {
        'WIP': '01. В работе',
        'SHARED': '02. На согласовании',
        'PUBLISHED': '03. Опубликовано',
        'ARCHIVE': '04. Архив',
    }
    zone_order_map = {
        'WIP': 1,
        'SHARED': 2,
        'PUBLISHED': 3,
        'ARCHIVE': 4,
    }
    actor = user or project.created_by
    for zone in missing_zones:
        # Переименованная зона до миграции: ищем корневую папку с тем же
        # zone, но не структурный корень ИРД/ПД/РД.
        existing = Folder.objects.filter(
            project=project,
            parent__isnull=True,
            section__isnull=True,
            zone=zone,
            is_deleted=False,
        ).exclude(name__in=ROOT_SUPERFOLDER_NAMES).first()
        if existing is not None:
            if not existing.is_system_zone:
                existing.is_system_zone = True
                existing.save(update_fields=['is_system_zone'])
            continue
        Folder.objects.create(
            project=project,
            section=None,
            parent=None,
            name=zone_name_map[zone],
            order=zone_order_map[zone],
            zone=zone,
            is_system_zone=True,
            created_by=actor,
        )
    return True


def generate_gost_code(
    base_code: str,
    section_code: str,
    *folder_codes: str | None,
) -> str:
    """Собирает ГОСТ-шифр из непустых частей через дефис.

    Папки передаются в порядке вложенности: ``parent_code, child_code``.
    """
    parts = [
        (value or '').strip()
        for value in (base_code, section_code, *folder_codes)
        if (value or '').strip()
    ]
    return '-'.join(parts)


def create_project(
    company: Company,
    code: str,
    name: str,
    customer: str,
    gip: User | None = None,
    created_by: User | None = None,
    base_code: str = '',
) -> Project:
    """Создает проект в указанной компании.

    Создатель по умолчанию — ГИП. Создатель и ГИП должны принадлежать компании,
    если они не superuser.
    """
    actor = created_by or gip
    if actor is None:
        raise ValueError("Не указан создатель проекта")
    if not can_create_project(actor):
        raise PermissionDenied("Нет прав на создание проекта")

    _validate_gip_role(gip)

    code = (code or '').strip()
    name = (name or '').strip()
    if not code or not name:
        raise ValueError("Шифр и название проекта обязательны")

    if not _check_company_access(actor, company.pk):
        raise PermissionDenied("Нет прав на создание проекта в этой компании")
    if gip is not None and not _check_company_access(gip, company.pk):
        raise PermissionDenied("ГИП должен принадлежать компании проекта")

    existing = Project.objects.filter(code=code).first()
    restored = False
    if existing is not None:
        if not existing.is_deleted:
            raise ValueError("Проект с таким шифром уже существует")
        if existing.company_id != company.pk:
            raise ValueError(
                "Проект с таким шифром находится в корзине другой компании"
            )
        # Мягко удалённый проект восстанавливаем и перезаписываем карточку.
        project = existing
        old_base_code = project.base_code
        project.company = company
        project.base_code = (base_code or '').strip()
        project.name = name
        project.customer = customer
        project.gip = gip
        project.created_by = actor
        project.status = 'active'
        project.is_deleted = False
        project.deleted_at = None
        project.save(
            update_fields=[
                'company',
                'base_code',
                'name',
                'customer',
                'gip',
                'created_by',
                'status',
                'is_deleted',
                'deleted_at',
            ]
        )
        if old_base_code != project.base_code:
            _refresh_project_full_codes(project, old_base_code)
        restored = True
    else:
        project = Project.objects.create(
            company=company,
            code=code,
            base_code=(base_code or '').strip(),
            name=name,
            customer=customer,
            gip=gip,
            status='active',
            created_by=actor,
        )

    _ensure_project_zones(project, actor)
    project._restored_from_trash = restored
    return project


def can_create_project(user: User) -> bool:
    """Может ли пользователь создавать проекты через интерфейс."""
    if user.is_superuser:
        return True
    profile = getattr(user, 'userprofile', None)
    return profile is not None and profile.role in ('admin', 'gip')


def can_edit_project(user: User, project: Project) -> bool:
    """Редактирование: суперюзер, админ компании или ГИП своего проекта."""
    return can_manage_project_participants(user, project)


def can_archive_project(user: User, project: Project) -> bool:
    """Архивирование: только суперюзер или админ компании."""
    if user.is_superuser:
        return True
    profile = getattr(user, 'userprofile', None)
    return (
        profile is not None
        and profile.role == 'admin'
        and _check_company_access(user, project.company_id)
    )


def can_delete_project(user: User, project: Project) -> bool:
    """Мягкое удаление: только суперюзер или админ компании."""
    return can_archive_project(user, project)


def can_restore_project(user: User, project: Project) -> bool:
    """Восстановление: только суперюзер или админ компании."""
    return can_archive_project(user, project)


_UNSET = object()


def _refresh_project_full_codes(
    project: Project,
    old_base_code: str | None = None,
) -> None:
    """Пересчитывает ГОСТ-шифры разделов и папок после смены base_code."""
    for section in project.sections.all():
        section.full_code = generate_gost_code(project.base_code, section.code)
        section.save(update_fields=['full_code'])

    def refresh(folder: Folder) -> None:
        old_code = (folder.code or '').strip()
        if old_base_code and old_code.startswith(f'{old_base_code}-'):
            # Абсолютный пользовательский шифр: меняем только префикс базы.
            folder.code = f'{project.base_code}{old_code[len(old_base_code):]}'
            folder.full_code = folder.code
            folder.save(update_fields=['code', 'full_code'])
        else:
            folder.full_code = _folder_full_code(
                project,
                folder.section,
                folder.parent,
                folder.code,
            )
            folder.save(update_fields=['full_code'])
        for child in Folder.objects.filter(parent=folder, is_deleted=False):
            refresh(child)

    for root in Folder.objects.filter(
        project=project,
        parent__isnull=True,
        is_deleted=False,
    ):
        if _is_canonical_zone_folder(root):
            continue
        refresh(root)


def update_project(
    user: User,
    project: Project,
    *,
    code: str | None = None,
    name: str | None = None,
    customer: str | None = None,
    base_code: str | None = None,
    status: str | None = None,
    gip: User | None | object = _UNSET,
) -> Project:
    """Обновляет карточку проекта с проверкой прав."""
    if not can_edit_project(user, project):
        raise PermissionDenied("Нет прав на редактирование проекта")

    update_fields = []

    if code is not None:
        code = code.strip()
        if not code:
            raise ValueError("Шифр проекта обязателен")
        if Project.objects.filter(code=code).exclude(pk=project.pk).exists():
            raise ValueError("Проект с таким шифром уже существует")
        project.code = code
        update_fields.append('code')

    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Название проекта обязательно")
        project.name = name
        update_fields.append('name')

    if customer is not None:
        project.customer = customer.strip()
        update_fields.append('customer')

    base_changed = False
    if base_code is not None:
        base_code = base_code.strip()
        if base_code != project.base_code:
            project.base_code = base_code
            update_fields.append('base_code')
            base_changed = True

    if status is not None and status != project.status:
        if not can_archive_project(user, project):
            raise PermissionDenied("Менять статус проекта может только администратор")
        if status not in dict(Project.STATUS_CHOICES):
            raise ValueError("Неверный статус проекта")
        project.status = status
        update_fields.append('status')

    if gip is not _UNSET:
        gip_changed = (gip.pk if gip is not None else None) != project.gip_id
        if gip is not None and gip_changed:
            if not _check_company_access(gip, project.company_id):
                raise PermissionDenied("ГИП должен принадлежать компании проекта")
            _validate_gip_role(gip)
        project.gip = gip
        update_fields.append('gip')

    if update_fields:
        project.save(update_fields=update_fields)
    if base_changed:
        _refresh_project_full_codes(project)
    return project


def archive_project(user: User, project: Project) -> Project:
    """Переводит проект в архив."""
    if not can_archive_project(user, project):
        raise PermissionDenied("Нет прав на архивирование проекта")
    if project.status != 'archived':
        project.status = 'archived'
        project.save(update_fields=['status'])
    return project


@transaction.atomic
def delete_project(user: User, project: Project) -> Project:
    """Мягко удаляет проект и его активное содержимое."""
    if not can_delete_project(user, project):
        raise PermissionDenied("Нет прав на удаление проекта")
    now = timezone.now()
    project.is_deleted = True
    project.deleted_at = now
    project.status = 'archived'
    project.save(update_fields=['is_deleted', 'deleted_at', 'status'])

    Folder.objects.filter(
        project=project,
        is_deleted=False,
    ).update(
        is_deleted=True,
        deleted_at=now,
        deleted_with_project=True,
    )
    Document.objects.filter(
        project=project,
        is_deleted=False,
    ).update(
        is_deleted=True,
        deleted_at=now,
        deleted_with_project=True,
    )
    return project


@transaction.atomic
def restore_project(user: User, project: Project) -> Project:
    """Восстанавливает проект и его содержимое, удалённое вместе с ним."""
    if not can_restore_project(user, project):
        raise PermissionDenied("Нет прав на восстановление проекта")
    if not project.is_deleted:
        raise ValueError("Проект не находится в корзине")

    project.is_deleted = False
    project.deleted_at = None
    project.status = 'active'
    project.save(update_fields=['is_deleted', 'deleted_at', 'status'])

    Folder.objects.filter(
        project=project,
        is_deleted=True,
        deleted_with_project=True,
    ).update(
        is_deleted=False,
        deleted_at=None,
        deleted_with_project=False,
    )
    Document.objects.filter(
        project=project,
        is_deleted=True,
        deleted_with_project=True,
    ).update(
        is_deleted=False,
        deleted_at=None,
        deleted_with_project=False,
    )
    return project


def create_section(
    project: Project,
    code: str,
    name: str,
    order: int,
    user: User | None = None,
    full_code: str = '',
    force: bool = False,
) -> Section:
    """Создаёт раздел проекта."""
    if user is not None and not force:
        _ensure_content_manager(user, project)
    code = (code or '').strip()
    name = (name or '').strip()
    if not code or not name:
        raise ValueError("Код и название раздела обязательны")
    if Section.objects.filter(project=project, code=code).exists():
        raise ValueError("Раздел с таким кодом уже существует")
    section_full_code = (full_code or '').strip() or generate_gost_code(
        project.base_code,
        code,
    )
    return Section.objects.create(
        project=project,
        code=code,
        full_code=section_full_code,
        name=name,
        order=int(order or 0),
    )


def _ensure_content_manager(user: User, project: Project) -> None:
    _ensure_project_access(user, project)
    if not can_manage_project_participants(user, project):
        raise PermissionDenied("Нет прав на управление структурой проекта")


def _folder_code_chain(folder: Folder | None) -> list[str]:
    """Коды папки и всех её родителей в порядке от корня к потомку."""
    codes = []
    current = folder
    while current is not None:
        code = (current.code or '').strip()
        if code:
            codes.append(code)
        current = current.parent
    codes.reverse()
    return codes


def _folder_full_code(
    project: Project,
    section: Section | None,
    parent: Folder | None,
    code: str,
) -> str:
    """Полный ГОСТ-шифр папки.

    Для корневой папки раздела: ``base_code-section.code-code``.
    Для подпапки: ``parent.full_code-code``.
    """
    code = (code or '').strip()
    if (
        code
        and project.base_code
        and code.startswith(f'{project.base_code}-')
    ):
        # Пользователь ввёл полный шифр (например, 20138-ПЗ1) — сохраняем его.
        return code
    if parent is not None and parent.full_code:
        return f'{parent.full_code}-{code}' if code else parent.full_code
    # Fallback для старых папок без full_code: собираем цепочку кодов.
    codes = _folder_code_chain(parent)
    if code:
        codes.append(code)
    return generate_gost_code(
        project.base_code,
        section.code if section is not None else '',
        *codes,
    )


def _folder_subtree_ids(folder: Folder) -> set[int]:
    ids = {folder.pk}
    frontier = [folder.pk]
    while frontier:
        children = list(
            Folder.objects.filter(parent_id__in=frontier).values_list('pk', flat=True)
        )
        frontier = [pk for pk in children if pk not in ids]
        ids.update(frontier)
    return ids


def _folder_code_exists(
    project: Project,
    section: Section | None,
    code: str,
    parent: Folder | None = None,
    exclude: Folder | None = None,
) -> bool:
    if not code:
        return False
    qs = Folder.objects.filter(
        project=project,
        section=section,
        parent=parent,
        code=code,
        is_deleted=False,
    )
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.exists()


def get_folder_mark_id(folder: Folder | None) -> str | None:
    """Стабильный идентификатор марки/раздела папки.

    Для старой структуры марка определяется по Section (АР, КР и т.п.).
    Для новой структуры все папки сидят в COMMON, поэтому марка — это
    верхнеуровневая папка внутри суперпапки ПД/РД/ИРД.
    """
    if folder is None:
        return None

    section = folder.section
    if section is not None and section.code != 'COMMON':
        return f'section:{section.code or section.pk}'

    current = folder
    while current is not None:
        parent = current.parent
        if (
            parent is not None
            and parent.parent_id is None
            and parent.name in ROOT_SUPERFOLDER_NAMES
        ):
            return f'folder:{current.pk}'
        current = parent
    return None


def _is_absolute_folder_code(project: Project, code: str) -> bool:
    base_code = (project.base_code or '').strip()
    code = (code or '').strip()
    return bool(base_code and code.startswith(f'{base_code}-'))


def _next_folder_code(
    project: Project,
    section: Section | None,
    parent: Folder | None = None,
) -> str:
    codes = set(
        Folder.objects.filter(
            project=project,
            section=section,
            parent=parent,
            code__gt='',
            is_deleted=False,
        ).values_list('code', flat=True)
    )
    index = 1
    while str(index).zfill(2) in codes:
        index += 1
    return str(index).zfill(2)


def create_folder(
    user: User,
    project: Project,
    name: str,
    section: Section | None = None,
    parent: Folder | None = None,
    code: str = '',
    full_code: str = '',
    force: bool = False,
) -> Folder:
    """Создаёт папку проекта или вложенную папку."""
    if not force:
        _ensure_content_manager(user, project)
    name = (name or '').strip()
    if not name:
        raise ValueError("Название папки обязательно")

    if section is not None and section.project_id != project.pk:
        raise ValueError("Раздел относится к другому проекту")

    if parent is not None:
        if parent.project_id != project.pk:
            raise ValueError("Родительская папка относится к другому проекту")
        if parent.is_deleted:
            raise ValueError("Нельзя создать папку внутри удалённой папки")
        if _is_canonical_zone_folder(parent) or parent.zone != 'WIP':
            raise ValueError(
                "Нельзя создавать папки внутри виртуальных зон — "
                "используйте разделы проекта"
            )
        if parent.section_id is not None:
            if section is not None and section.pk != parent.section_id:
                raise ValueError("Раздел папки не совпадает с разделом родителя")
            section = parent.section

    max_order = Folder.objects.filter(project=project, parent=parent).aggregate(
        Max('order')
    )['order__max']
    folder_zone = 'WIP'
    if parent is not None:
        folder_zone = parent.zone or 'WIP'
    code = (code or '').strip()
    absolute_code = _is_absolute_folder_code(project, code)
    if not code:
        # Автонумерация — только для корневых папок раздела/проекта.
        # Подпапка без кода сохраняется без номера; её шифр = шифр родителя.
        if parent is None:
            code = _next_folder_code(project, section, parent)
    elif _folder_code_exists(project, section, code, parent=parent):
        raise ValueError('Папка с таким кодом уже существует в этой папке')
    folder_full_code = (full_code or '').strip() or (
        code if absolute_code else _folder_full_code(
            project,
            section,
            parent,
            code,
        )
    )
    return Folder.objects.create(
        project=project,
        section=section,
        parent=parent,
        name=name,
        code=code,
        full_code=folder_full_code,
        order=(max_order or 0) + 1,
        zone=folder_zone,
        created_by=user,
    )


def rename_folder(
    user: User,
    folder: Folder,
    name: str,
    code: str | None = None,
) -> Folder:
    """Переименовывает папку и, при необходимости, меняет ГОСТ-код."""
    _ensure_content_manager(user, folder.project)
    name = (name or '').strip()
    if not name:
        raise ValueError("Название папки обязательно")
    update_fields = ['name']
    folder.name = name
    if code is not None:
        code = (code or '').strip()
        if not code and folder.parent is None:
            raise ValueError('Код папки обязателен')
        if code and _folder_code_exists(
            folder.project,
            folder.section,
            code,
            parent=folder.parent,
            exclude=folder,
        ):
            raise ValueError('Папка с таким кодом уже существует в этой папке')
        folder.code = code
        update_fields.append('code')
    folder.save(update_fields=update_fields)
    if code is not None:
        # Меняется собственный шифр и шифры всех вложенных папок.
        _refresh_folder_subtree(folder, folder.section)
    return folder


def rename_section(
    user: User,
    section: Section,
    name: str,
    code: str | None = None,
) -> Section:
    """Переименовывает раздел и пересчитывает шифры его папок."""
    _ensure_content_manager(user, section.project)
    name = (name or '').strip()
    if not name:
        raise ValueError("Название раздела обязательно")
    section.name = name
    update_fields = ['name']
    if code is not None:
        code = (code or '').strip()
        if not code:
            raise ValueError("Код раздела обязателен")
        section.code = code
        section.full_code = generate_gost_code(section.project.base_code, code)
        update_fields.extend(['code', 'full_code'])
    section.save(update_fields=update_fields)
    for folder in Folder.objects.filter(
        section=section, parent__isnull=True, is_deleted=False
    ):
        _refresh_folder_subtree(folder, section)
    return section


def get_folder_tree(user: User, project: Project) -> list[Folder]:
    """Возвращает корневые папки проекта с вложенными children_tree."""
    _ensure_project_access(user, project)
    accessible_sections = get_accessible_sections(user, project)
    folders = list(
        Folder.objects.filter(
            project=project,
            is_deleted=False,
        )
        .filter(Q(section__in=accessible_sections) | Q(section__isnull=True))
        # Спринт 8: шаблон дерева обращается к folder.parent — без
        # select_related это запрос на каждый узел.
        .select_related(
            'section',
            'parent',
            'parent__parent',
            'parent__parent__parent',
        )
        .annotate(
            document_count=Count(
                'documents',
                filter=Q(documents__is_deleted=False),
                distinct=True,
            )
        )
        .order_by('order', 'name')
    )
    by_parent = defaultdict(list)
    for folder in folders:
        folder.children_tree = []
        by_parent[folder.parent_id].append(folder)
    for folder in folders:
        folder.children_tree = by_parent[folder.pk]

    def _fill_subtree_counts(node: Folder) -> int:
        total = node.document_count
        for child in node.children_tree:
            total += _fill_subtree_counts(child)
        node.subtree_document_count = total
        return total

    for folder in by_parent[None]:
        _fill_subtree_counts(folder)

    return by_parent[None]


def ru_plural(number, one: str, few: str, many: str) -> str:
    """Русская плюрализация с исключением 11–14 (1 замечание, 2 замечания,
    5 замечаний). Django-фильтр pluralize трёх форм не поддерживает
    (возвращает '' при двух запятых в аргументе)."""
    number = abs(int(number)) % 100
    if 11 <= number <= 14:
        return many
    last = number % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def get_flat_folders(user: User, project: Project):
    """Плоский список активных папок для выбора при перемещении."""
    _ensure_project_access(user, project)
    accessible_sections = get_accessible_sections(user, project)
    return (
        Folder.objects.filter(
            project=project,
            is_deleted=False,
        )
        .filter(Q(section__in=accessible_sections) | Q(section__isnull=True))
        .select_related('section')
        .order_by('section__order', 'order', 'name')
    )


def move_document(
    user: User,
    document: Document,
    folder: Folder | None,
    allow_section_change: bool = False,
) -> Document:
    """Перемещает документ в папку проекта."""
    if not check_write_permission(user, document.section):
        raise PermissionDenied("Нет прав на перемещение документа")
    if document.is_deleted:
        raise ValueError("Нельзя перемещать документ из корзины")

    # Проверка прав на покидание текущей зоны: смена effective_zone
    # через DnD запрещена всем, включая администратора. Для контролируемых
    # переходов между зонами есть workflow-действия (transition_document_zone).
    current_zone = _effective_zone(document)
    target_zone = (
        folder.zone
        if folder is not None
        else _ZONE_BY_STATUS.get(document.status, 'WIP')
    )
    if (
        current_zone in ('ARCHIVE', 'PUBLISHED', 'SHARED')
        and target_zone != current_zone
    ):
        zone_name = (
            document.folder.get_zone_display()
            if document.folder is not None
            else dict(Folder.ZONE_CHOICES).get(current_zone, current_zone)
        )
        raise PermissionDenied(
            f"Документ в зоне «{zone_name}» — смена зоны через "
            "перемещение запрещена"
        )

    # Папки внутри зон не должны становиться обходным путём смены зоны.
    if (
        folder is not None
        and not _is_canonical_zone_folder(folder)
        and folder.zone != 'WIP'
        and folder.zone != current_zone
    ):
        raise PermissionDenied(
            "Нельзя перемещать документы между зонами через папки — "
            "используйте workflow-кнопки"
        )

    if folder is not None:
        if folder.project_id != document.project_id:
            raise ValueError("Папка относится к другому проекту")
        if folder.is_deleted:
            raise ValueError("Нельзя переместить документ в удалённую папку")
        if folder.section_id is None:
            if not _is_canonical_zone_folder(folder):
                raise ValueError("Папка относится к другому разделу")
            section_change = False
        else:
            section_change = folder.section_id != document.section_id

        current_mark_id = get_folder_mark_id(document.folder)
        target_mark_id = get_folder_mark_id(folder)
        mark_change = bool(
            current_mark_id
            and target_mark_id
            and current_mark_id != target_mark_id
        )

        if (section_change or mark_change) and not allow_section_change:
            raise ValueError(
                "Смена раздела через обычное перемещение запрещена"
            )

        if section_change and not check_write_permission(user, folder.section):
            profile = getattr(user, 'userprofile', None)
            if profile is not None and profile.role in (
                'developer',
                'contractor',
            ):
                raise PermissionDenied(
                    "У вас нет прав на перемещение в этот раздел"
                )
            raise PermissionDenied(
                "Недостаточно прав для перемещения между разделами"
            )

        target_section = folder.section or document.section
        # Проверка прав на запись в целевую зону
        if not check_zone_permission(user, folder, target_section):
            zone_name = folder.get_zone_display()
            raise PermissionDenied(f"Нет прав на запись в зону «{zone_name}»")

    document.folder = folder
    update_fields = ['folder', 'updated_at']
    if (
        folder is not None
        and folder.section_id is not None
        and folder.section_id != document.section_id
    ):
        document.section = folder.section
        update_fields.append('section')

    # Автоматическое изменение статуса и кода пригодности
    # только для канонических зон.
    target_status = _status_for_zone(folder)
    target_suitability = _suitability_for_zone(folder)
    if target_status is not None and (
        target_status != document.status
        or target_suitability != document.suitability_code
    ):
        validate_transition(
            document,
            target_status,
            user,
            target_suitability,
        )
    if (
        _approval_target_changes(
            document,
            target_status,
            target_suitability,
            target_zone,
        )
        and _has_open_issues(document)
    ):
        raise ValueError(
            "Нельзя переместить документ с открытыми замечаниями "
            "в опубликованную зону"
        )
    if target_status is not None and target_status != document.status:
        document.status = target_status
        update_fields.append('status')

    if (
        target_suitability is not None
        and target_suitability != document.suitability_code
    ):
        document.suitability_code = target_suitability
        update_fields.append('suitability_code')

    document._from_service = True
    document.save(update_fields=update_fields)
    return document


def move_documents(user: User, documents, folder: Folder | None) -> list[Document]:
    """Перемещает несколько документов, проверяя права для каждого."""
    with transaction.atomic():
        return [move_document(user, document, folder) for document in documents]


def move_document_to_section(user: User, document: Document, section: Section) -> Document:
    """Перемещает документ в корень другого раздела проекта."""
    if document.is_deleted:
        raise ValueError("Нельзя перемещать документ из корзины")
    if section.project_id != document.project_id:
        raise ValueError("Раздел относится к другому проекту")
    if not check_write_permission(user, document.section):
        raise PermissionDenied("Нет прав на перемещение документа")
    if not check_write_permission(user, section):
        raise PermissionDenied("Нет прав на запись в целевой раздел")

    # Спринт 5: перенос в другой раздел — это смена эффективной зоны на WIP.
    # Запрещаем выносить документы из канонических зон, как и в move_document:
    # иначе утверждённый документ «исчезает» из папки зоны через обходной путь.
    current_zone = _effective_zone(document)
    if current_zone in ('ARCHIVE', 'PUBLISHED', 'SHARED'):
        zone_name = (
            document.folder.get_zone_display()
            if document.folder is not None
            else dict(Folder.ZONE_CHOICES).get(current_zone, current_zone)
        )
        raise PermissionDenied(
            f"Документ в зоне «{zone_name}» — перенос в другой раздел "
            "запрещён, используйте workflow-кнопки"
        )

    document.section = section
    document.folder = None
    document.save(update_fields=['section', 'folder', 'updated_at'])
    return document


def _unique_copy_code(document: Document, section: Section) -> str:
    base = f'{document.code} (копия)'
    candidate = base[:100]
    counter = 2
    while Document.objects.filter(
        project=document.project,
        section=section,
        code=candidate,
    ).exists():
        suffix = f' (копия {counter})'
        candidate = f'{document.code[:max(1, 100 - len(suffix))]}{suffix}'
        counter += 1
    return candidate


def copy_document(
    user: User,
    document: Document,
    folder: Folder | None,
    section: Section | None,
    file_storage,
) -> Document:
    """Создаёт копию документа со всеми версиями и файлами."""
    if document.is_deleted:
        raise ValueError("Нельзя копировать документ из корзины")
    if not check_read_permission(user, document):
        raise PermissionDenied("Нет прав на копирование документа")

    target_section = section or (
        folder.section if folder is not None and folder.section_id else document.section
    )
    if target_section.project_id != document.project_id:
        raise ValueError("Раздел относится к другому проекту")
    if not check_write_permission(user, target_section):
        raise PermissionDenied("Нет прав на запись в целевой раздел")

    if folder is not None:
        if folder.project_id != document.project_id:
            raise ValueError("Папка относится к другому проекту")
        if folder.is_deleted:
            raise ValueError("Нельзя скопировать документ в удалённую папку")
        if folder.section_id is not None and folder.section_id != target_section.pk:
            raise ValueError("Папка относится к другому разделу")

        # Проверка прав по зоне
        if not check_zone_permission(user, folder, target_section):
            zone_name = folder.get_zone_display()
            raise PermissionDenied(f"Нет прав на запись в зону «{zone_name}»")

    versions = list(document.versions.order_by('version_number'))
    with transaction.atomic():
        copied = Document.objects.create(
            project=document.project,
            section=target_section,
            folder=folder,
            code=_unique_copy_code(document, target_section),
            name=f'{document.name} (копия)'[:255],
            doc_type=document.doc_type,
            # Спринт 5: статус-триплет синхронизируем с целевой папкой,
            # иначе копия утверждённого документа попадает в WIP со статусом
            # approved и ломает маппинг статус-зона и сегмент-контрол.
            status=(
                ZONE_STATUS_MAP.get(folder.zone, 'draft')
                if folder is not None else 'draft'
            ),
            suitability_code=(
                ZONE_SUITABILITY_MAP.get(folder.zone, 'S0')
                if folder is not None else 'S0'
            ),
            created_by=user,
        )
        current_copy = None
        for version in versions:
            extension = version.file.name.rsplit('.', 1)
            suffix = f'.{extension[1]}' if len(extension) == 2 else ''
            target_path = (
                f'documents/{timezone.now():%Y/%m}/'
                f'{uuid.uuid4().hex}{suffix}'
            )
            copied_path = file_storage.copy(version.file.name, target_path)
            copied_version = DocumentVersion.objects.create(
                document=copied,
                version_number=version.version_number,
                file=copied_path,
                file_size=version.file_size,
                file_hash=version.file_hash,
                comment=version.comment,
                uploaded_by=user,
            )
            if document.current_version_id == version.pk:
                current_copy = copied_version

        if current_copy is not None:
            copied.current_version = current_copy
            copied.save(update_fields=['current_version'])

    return copied


def _refresh_folder_subtree(folder: Folder, section: Section | None) -> None:
    folder.section = section
    folder.full_code = _folder_full_code(
        folder.project,
        section,
        folder.parent,
        folder.code,
    )
    folder.save(update_fields=['section', 'full_code'])
    for child in Folder.objects.filter(parent=folder, is_deleted=False):
        _refresh_folder_subtree(child, section)


def move_folder(
    user: User,
    folder: Folder,
    parent: Folder | None = None,
    section: Section | None = None,
    to_project_root: bool = False,
    allow_section_change: bool = False,
) -> Folder:
    """Перемещает папку в другую папку, раздел или в корень проекта."""
    _ensure_content_manager(user, folder.project)
    if folder.is_deleted:
        raise ValueError("Нельзя перемещать папку из корзины")

    if parent is not None:
        if parent.project_id != folder.project_id:
            raise ValueError("Папка относится к другому проекту")
        if parent.is_deleted:
            raise ValueError("Нельзя переместить папку в удалённую папку")
        if parent.pk == folder.pk:
            raise ValueError("Нельзя переместить папку в саму себя")
        if parent.pk in _folder_subtree_ids(folder):
            raise ValueError("Нельзя переместить папку в собственный потомок")
        if _is_canonical_zone_folder(parent) or parent.zone != 'WIP':
            raise ValueError(
                "Нельзя перемещать папки внутрь виртуальных зон"
            )
        new_section = parent.section
    elif section is not None:
        if section.project_id != folder.project_id:
            raise ValueError("Раздел относится к другому проекту")
        new_section = section
    elif to_project_root:
        new_section = None
    else:
        new_section = folder.section

    # Обычный DnD папок не должен менять раздел/марку: иначе папка
    # с документами может уехать в чужую марку.
    section_changed = (
        (new_section.pk if new_section is not None else None)
        != folder.section_id
    )
    if parent is not None:
        target_mark_id = get_folder_mark_id(parent)
    elif section is not None and section.code != 'COMMON':
        target_mark_id = f'section:{section.code or section.pk}'
    else:
        target_mark_id = None
    current_mark_id = get_folder_mark_id(folder)
    mark_changed = bool(
        current_mark_id
        and target_mark_id
        and current_mark_id != target_mark_id
    )
    if (section_changed or mark_changed) and not allow_section_change:
        raise ValueError(
            'Для смены марки используйте кнопку "Сменить раздел"'
        )

    subtree_ids = _folder_subtree_ids(folder)
    subtree_documents = Document.objects.filter(
        folder_id__in=subtree_ids,
        is_deleted=False,
    )
    if section_changed and new_section is None and subtree_documents.exists():
        raise ValueError(
            "Нельзя переместить папку с документами в корень без раздела"
        )

    if _folder_code_exists(
        folder.project,
        new_section,
        folder.code,
        parent=parent,
        exclude=folder,
    ):
        raise ValueError('Папка с таким кодом уже существует в этой папке')

    folder.parent = parent
    folder.save(update_fields=['parent'])
    _refresh_folder_subtree(folder, new_section)
    if section_changed and new_section is not None:
        subtree_documents.update(section=new_section)
    return folder


def reorder_folders(user: User, project: Project, folder_ids: list[int]) -> int:
    """Сохраняет порядок папок (порядок id в списке)."""
    _ensure_content_manager(user, project)
    valid_ids = []
    for raw in folder_ids:
        try:
            valid_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    existing = set(
        Folder.objects.filter(
            project=project,
            pk__in=valid_ids,
            is_deleted=False,
        ).values_list('pk', flat=True)
    )
    updated = 0
    for index, folder_id in enumerate(valid_ids):
        if folder_id not in existing:
            continue
        Folder.objects.filter(pk=folder_id).update(order=index)
        updated += 1
    return updated


def delete_document(user: User, document: Document) -> Document:
    """Мягко удаляет документ."""
    if not check_write_permission(user, document.section):
        raise PermissionDenied("Нет прав на удаление документа")
    if not document.is_deleted:
        document.is_deleted = True
        document.deleted_at = timezone.now()
        document.deleted_with_parent = False
        document.save(update_fields=['is_deleted', 'deleted_at', 'deleted_with_parent'])
    return document


def delete_documents(user: User, documents) -> int:
    """Мягко удаляет несколько документов."""
    count = 0
    with transaction.atomic():
        for document in documents:
            delete_document(user, document)
            count += 1
    return count


def restore_document(user: User, document: Document) -> Document:
    """Восстанавливает документ из корзины."""
    if not check_write_permission(user, document.section):
        raise PermissionDenied("Нет прав на восстановление документа")
    if document.folder_id and document.folder.is_deleted:
        document.folder = None
    document.is_deleted = False
    document.deleted_at = None
    document.deleted_with_parent = False
    document.save(update_fields=[
        'is_deleted',
        'deleted_at',
        'deleted_with_parent',
        'folder',
        'updated_at',
    ])
    return document


def delete_folder(user: User, folder: Folder) -> None:
    """Мягко удаляет папку, вложенные папки и документы."""
    _ensure_content_manager(user, folder.project)
    folder_ids = _folder_subtree_ids(folder)
    now = timezone.now()
    with transaction.atomic():
        Folder.objects.filter(
            pk__in=folder_ids,
            is_deleted=False,
        ).update(
            is_deleted=True,
            deleted_at=now,
            deleted_with_parent=True,
        )
        Document.objects.filter(
            folder_id__in=folder_ids,
            is_deleted=False,
        ).update(
            is_deleted=True,
            deleted_at=now,
            deleted_with_parent=True,
        )


def restore_folder(user: User, folder: Folder) -> None:
    """Восстанавливает папку, вложенные папки и документы."""
    _ensure_content_manager(user, folder.project)
    folder_ids = _folder_subtree_ids(folder)
    with transaction.atomic():
        Folder.objects.filter(pk=folder.pk).update(
            is_deleted=False,
            deleted_at=None,
            deleted_with_parent=False,
        )
        Folder.objects.filter(
            pk__in=folder_ids,
            deleted_with_parent=True,
        ).update(
            is_deleted=False,
            deleted_at=None,
            deleted_with_parent=False,
        )
        Document.objects.filter(
            folder_id__in=folder_ids,
            deleted_with_parent=True,
        ).update(
            is_deleted=False,
            deleted_at=None,
            deleted_with_parent=False,
        )


def generate_share_link(user: User, document: Document) -> str:
    """Генерирует/продлевает публичный токен документа на настроенный TTL."""
    if not _can_manage_share_link(user, document):
        raise PermissionDenied("Нет прав на создание ссылки")
    if document.is_deleted:
        raise ValueError("Нельзя создать ссылку на удалённый документ")

    now = timezone.now()
    update_fields = []
    if not document.public_link_token:
        document.public_link_token = uuid.uuid4()
        update_fields.append('public_link_token')
    if (
        document.public_link_expires_at is None
        or document.public_link_expires_at <= now
    ):
        document.public_link_expires_at = now + timedelta(
            days=settings.SHARE_LINK_TTL_DAYS
        )
        update_fields.append('public_link_expires_at')
    if update_fields:
        document.save(update_fields=update_fields)
    return str(document.public_link_token)


def _can_manage_share_link(user: User, document: Document) -> bool:
    """Владелец документа, ГИП проекта или админ компании."""
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not check_read_permission(user, document):
        return False
    profile = getattr(user, 'userprofile', None)
    if profile is not None and profile.role == 'admin':
        return True
    if document.created_by_id == user.pk:
        return True
    return _is_project_gip(user, document.project)


def revoke_share_link(user: User, document: Document) -> Document:
    """Отзывает публичную ссылку документа."""
    if not _can_manage_share_link(user, document):
        raise PermissionDenied("Нет прав на отзыв ссылки")
    if document.is_deleted:
        raise ValueError("Нельзя отозвать ссылку у удалённого документа")

    document.public_link_token = None
    document.public_link_expires_at = None
    document.save(update_fields=[
        'public_link_token',
        'public_link_expires_at',
    ])
    return document


def get_trash(user: User, project: Project) -> dict:
    """Удалённые документы и папки проекта для корзины."""
    _ensure_content_manager(user, project)
    return {
        'documents': Document.objects.filter(
            project=project,
            is_deleted=True,
        ).select_related('section', 'current_version').order_by('-deleted_at'),
        'folders': Folder.objects.filter(
            project=project,
            is_deleted=True,
        ).select_related('section', 'parent').order_by('-deleted_at'),
    }


def can_purge_trash(user: User, project: Project) -> bool:
    """Очищать корзину может только администратор компании (или superuser)."""
    if user.is_superuser:
        return True

    profile = getattr(user, 'userprofile', None)
    if profile is None or profile.role != 'admin':
        return False

    return _check_company_access(user, project.company_id)


def empty_trash(user: User, project: Project, file_storage) -> dict:
    """Безвозвратно удаляет корзину проекта.

    Физические файлы удаляются через порт ``FileStorage``, затем записи
    версий, документов и папок удаляются из базы данных.
    """
    _ensure_project_access(user, project)
    if not can_purge_trash(user, project):
        raise PermissionDenied("Очистить корзину может только администратор")

    documents = Document.objects.filter(project=project, is_deleted=True)
    document_count = documents.count()
    folder_count = Folder.objects.filter(project=project, is_deleted=True).count()

    versions = list(
        DocumentVersion.objects.filter(document__in=documents).only('file')
    )
    # Спринт 5: маркапы замечаний удаляемых документов тоже подлежат очистке,
    # иначе в issues/crops/ накапливается мусор, на который нет ссылок.
    crop_paths = list(
        Issue.objects.filter(document__in=documents)
        .exclude(crop_image='')
        .values_list('crop_image', flat=True)
    )
    stale_paths = [
        version.file.name for version in versions if version.file
    ] + list(crop_paths)

    with transaction.atomic():
        Document.objects.filter(project=project, is_deleted=True).delete()
        Folder.objects.filter(project=project, is_deleted=True).delete()

    # Спринт 5: файлы удаляем только после фиксации транзакции (on_commit).
    # Раньше физическое удаление шло до БД: падение БД оставляло записи
    # без файлов. Теперь наоборот — при откате файлы остаются на месте.
    def _cleanup_files() -> None:
        for path in stale_paths:
            try:
                file_storage.delete(path)
            except OSError:
                continue

    transaction.on_commit(_cleanup_files)

    return {'documents': document_count, 'folders': folder_count}


def get_documents_for_archive(user: User, document_ids) -> QuerySet:
    """Документы для ZIP-архива с проверкой доступа."""
    ids = [value for value in document_ids if str(value).isdigit()]
    return (
        get_accessible_documents(user)
        .filter(pk__in=ids, is_deleted=False)
        .select_related('current_version', 'section')
        .order_by('section__order', 'code')
    )


def get_folder_documents(user: User, folder: Folder) -> QuerySet:
    """Документы папки и вложенных папок для ZIP-архива."""
    _ensure_project_access(user, folder.project)
    if folder.is_deleted:
        raise ValueError("Папка находится в корзине")
    folder_ids = _folder_subtree_ids(folder)
    return (
        get_accessible_documents(user)
        .filter(
            project=folder.project,
            folder_id__in=folder_ids,
            is_deleted=False,
        )
        .select_related('current_version', 'section')
        .order_by('section__order', 'code')
    )


def check_write_permission(user: User, section: Section) -> bool:
    """Проверка права на запись в раздел.

    - admin — всегда может писать в своей компании.
    - gip — может писать только в свои проекты (через Project.gip или ProjectMembership).
    - developer — может писать только в разрешенные разделы (allowed_sections).
    - contractor — может писать только в выданные разделы (allowed_sections).
    - checker, normocontrol, customer, viewer — только чтение.

    Любая запись ограничена компанией пользователя.
    """
    if user.is_superuser:
        return True

    profile = getattr(user, 'userprofile', None)
    if profile is None:
        # Профиля нет — отказ
        return False

    if not _check_company_access(user, section.project.company_id):
        return False

    role = profile.role

    if role == 'admin':
        return True

    if role == 'gip':
        project = section.project
        # ГИП может писать, если его gip_id совпадает с gip проекта
        # ИЛИ если есть запись ProjectMembership с ролью gip
        if project.gip_id == user.id:
            return True
        if project.memberships.filter(user=user, role_in_project='gip').exists():
            return True
        return False

    if role in ('developer', 'contractor'):
        return section in profile.allowed_sections.all()

    # checker, normocontrol, customer, viewer и прочие — не могут писать
    return False


def check_zone_permission(
    user: User,
    folder: Folder | None,
    section: Section | None = None,
    zone: str | None = None,
) -> bool:
    """Проверка прав записи с учётом зоны папки или виртуальной зоны.

    Правила зон:
    - WIP: разработчики, смежники, ГИП, админ (через check_write_permission).
    - SHARED: только ГИП и админ.
    - PUBLISHED: только админ.
    - ARCHIVE: никто не может писать (только чтение).
    - folder=None и zone=None: стандартные правила check_write_permission.
    """
    project = (
        folder.project
        if folder is not None
        else (section.project if section is not None else None)
    )
    actual_zone = zone or (folder.zone if folder is not None else None)
    if actual_zone is None:
        # Нет зоны — стандартная проверка по секции
        if section is not None:
            return check_write_permission(user, section)
        return True

    if user.is_superuser:
        return True

    profile = getattr(user, 'userprofile', None)
    if profile is None:
        return False

    # ARCHIVE — админ может перемещать документы (архивировать)
    # Но загрузка новых версий в документ из ARCHIVE блокируется отдельно в upload_document
    if actual_zone == 'ARCHIVE':
        return profile.role == 'admin'

    # PUBLISHED — только админ компании
    if actual_zone == 'PUBLISHED':
        return profile.role == 'admin'

    # SHARED — только ГИП проекта и админ
    if actual_zone == 'SHARED':
        if profile.role == 'admin':
            return True
        if profile.role == 'gip' and project is not None:
            if project.gip_id == user.id:
                return True
            if project.memberships.filter(
                user=user, role_in_project='gip'
            ).exists():
                return True
        return False

    # WIP — стандартные правила записи
    if profile.role in ('viewer', 'checker', 'normocontrol', 'customer'):
        return False
    if profile.role == 'admin':
        return True
    if profile.role == 'gip':
        if project is None:
            return False
        if project.gip_id == user.id:
            return True
        if project.memberships.filter(
            user=user, role_in_project='gip'
        ).exists():
            return True
        return False
    if profile.role in ('developer', 'contractor'):
        # Разработчик и смежник пишут в WIP только в выданные разделы
        if section is not None:
            return section in profile.allowed_sections.all()
        return True

    return False


def check_read_permission(user: User, document: Document) -> bool:
    """Проверка права на чтение документа.

    - admin и gip (в своих проектах) читают всё.
    - developer читает всё в своих проектах.
    - checker, normocontrol, customer, viewer читают всё в своей компании.
    - contractor читает только если раздел документа в allowed_sections.
    """
    if user.is_superuser:
        return True

    profile = getattr(user, 'userprofile', None)
    if profile is None:
        return False

    if not _check_company_access(user, document.project.company_id):
        return False

    role = profile.role
    project = document.project

    if role == 'admin':
        return True

    if role == 'gip':
        if project.gip_id == user.id:
            return True
        return project.memberships.filter(
            user=user, role_in_project='gip'
        ).exists()

    if role == 'developer':
        return project.memberships.filter(
            user=user, role_in_project__in=('gip', 'developer')
        ).exists()

    if role in ('checker', 'normocontrol', 'customer', 'viewer'):
        return True

    if role == 'contractor':
        # contractor читает только разрешенные разделы
        return document.section in profile.allowed_sections.all()

    return False


def get_readable_documents(user: User, project: Project | None = None):
    """Документы, прошедшие реальную проверку чтения (с учётом членства).

    Спринт 3 (P2): раньше каждый документ прогонялся через
    check_read_permission — по запросу к базе на каждый чек (N+1 на
    реестре, Excel-экспорте и палитре). Правила те же, но переведены
    на ролевые SQL-фильтры: одна компания уже отсечена
    get_accessible_documents, дальше — не более одного запроса на роль:

    - admin / checker / normocontrol / customer / viewer — читают всё
      в своей компании (get_accessible_documents уже ограничил);
    - gip — свой проект или членство role_in_project='gip';
    - developer — членство role_in_project in (gip, developer);
    - contractor — get_accessible_documents уже ограничил разделами
      allowed_sections, что и есть правило чтения contractor'а.

    Расхождение с check_read_permission покрывает тест сервиса
    (issues/tests::RightsServiceTests) — при изменении правил проверять
    обе функции синхронно.
    """
    qs = get_accessible_documents(user)
    if project is not None:
        qs = qs.filter(project=project)
    qs = qs.select_related('project', 'section')

    if user.is_superuser:
        return qs

    profile = getattr(user, 'userprofile', None)
    if profile is None:
        return Document.objects.none()

    role = profile.role
    if role == 'admin':
        return qs
    if role in ('checker', 'normocontrol', 'customer', 'viewer'):
        return qs
    if role == 'gip':
        return qs.filter(
            Q(project__gip_id=user.id)
            | Q(project__memberships__user=user,
                project__memberships__role_in_project='gip')
        ).distinct()
    if role == 'developer':
        return qs.filter(
            project__memberships__user=user,
            project__memberships__role_in_project__in=('gip', 'developer'),
        ).distinct()
    if role == 'contractor':
        return qs
    return Document.objects.none()


# ============================================================================
# Волна 3: workflow зон ISO 19650 — «Подать на согласование» / «Опубликовать»
# ============================================================================

# Контролируемые переходы между зонами. В отличие от свободного DnD
# (move_document), здесь матрица ролей соответствует потоку ISO 19650:
# разработчик подаёт, проверяющий возвращает, ГИП публикует и архивирует.
ZONE_WORKFLOW_ACTIONS = {
    'submit_review': {
        'label': 'Подать на согласование',
        'icon': 'bi-arrow-right-circle',
        'from': 'WIP',
        'to': 'SHARED',
        'roles': ('admin', 'gip', 'developer', 'contractor'),
    },
    'return_wip': {
        'label': 'Вернуть в работу',
        'icon': 'bi-arrow-return-left',
        'from': 'SHARED',
        'to': 'WIP',
        'roles': ('admin', 'gip', 'checker', 'normocontrol'),
    },
    'publish': {
        'label': 'Утвердить',
        'icon': 'bi-shield-check',
        'from': 'SHARED',
        'to': 'PUBLISHED',
        'roles': ('admin', 'gip'),
    },
    'archive': {
        'label': 'В архив',
        'icon': 'bi-lock',
        'from': 'PUBLISHED',
        'to': 'ARCHIVE',
        'roles': ('admin',),
    },
}

_ZONE_BY_STATUS = {status: zone for zone, status in ZONE_STATUS_MAP.items()}


# FSM статусов документа. Ключи — текущий/целевой Document.status.
STATUS_TRANSITIONS = {
    'draft': frozenset({'in_review', 'approved', 'archived'}),
    'in_review': frozenset({'draft', 'approved'}),
    'approved': frozenset({'archived', 'draft', 'in_review'}),
    'archived': frozenset({'draft', 'in_review', 'approved'}),
}

TRANSITION_ROLES = {
    ('draft', 'in_review'): frozenset({
        'admin', 'gip', 'developer', 'contractor',
    }),
    ('draft', 'approved'): frozenset({'admin'}),
    ('draft', 'archived'): frozenset({'admin'}),
    ('in_review', 'draft'): frozenset({'admin', 'gip'}),
    ('in_review', 'approved'): frozenset({'admin', 'gip'}),
    ('approved', 'archived'): frozenset({'admin'}),
    ('approved', 'draft'): frozenset({'admin'}),
    ('approved', 'in_review'): frozenset({'admin'}),
    ('archived', 'draft'): frozenset({'admin'}),
    ('archived', 'in_review'): frozenset({'admin'}),
    ('archived', 'approved'): frozenset({'admin'}),
}

SUITABILITY_TRANSITIONS = {
    'S0': frozenset({'S0', 'S1', 'S2', 'S3', 'S4'}),
    'S1': frozenset({'S0', 'S1', 'S2', 'S3', 'S4'}),
    'S2': frozenset({'S0', 'S1', 'S2', 'S3', 'S4'}),
    'S3': frozenset({'S0', 'S1', 'S2', 'S3', 'S4', 'A', 'B'}),
    'S4': frozenset({'S3', 'S4', 'A', 'B'}),
    'A': frozenset({'A', 'B', 'CR'}),
    'B': frozenset({'A', 'B', 'CR'}),
    'CR': frozenset({'A', 'B', 'CR'}),
}

_STATUS_SUITABILITY = {
    status: ZONE_SUITABILITY_MAP[zone]
    for zone, status in ZONE_STATUS_MAP.items()
}


def _user_role(user: User) -> str | None:
    profile = getattr(user, 'userprofile', None) if user is not None else None
    return profile.role if profile is not None else None


def can_transition(
    document: Document,
    new_status: str,
    user: User,
    new_suitability: str | None = None,
) -> bool:
    """Проверяет допустимость перехода статуса/кода пригодности."""
    if user is None or not user.is_authenticated or document.is_deleted:
        return False
    if new_status not in dict(Document.STATUS_CHOICES):
        return False
    if (
        new_suitability is not None
        and new_suitability not in dict(Document.SUITABILITY_CHOICES)
    ):
        return False

    role = _user_role(user)
    is_admin = bool(
        user.is_superuser or role == 'admin'
    )
    if (
        is_admin
        and not user.is_superuser
        and not _check_company_access(user, document.project.company_id)
    ):
        return False

    target_suitability = (
        new_suitability
        if new_suitability is not None
        else _STATUS_SUITABILITY.get(new_status)
    )
    # T70: открытые замечания блокируют утверждение (кроме архивации).
    if (
        new_status == 'approved'
        or (
            target_suitability in ('S4', 'A')
            and new_status != 'archived'
        )
    ) and _has_open_issues(document):
        return False

    if new_status == document.status:
        if new_suitability is None or new_suitability == document.suitability_code:
            return True
        if is_admin:
            return True
        return new_suitability in SUITABILITY_TRANSITIONS.get(
            document.suitability_code, frozenset()
        )

    # Администратор/суперюзер может выходить из архива и выполнять
    # служебные переходы, но T70-проверка выше остаётся обязательной.
    if is_admin:
        return True

    if new_status not in STATUS_TRANSITIONS.get(document.status, frozenset()):
        return False
    allowed_roles = TRANSITION_ROLES.get(
        (document.status, new_status), frozenset()
    )
    if role not in allowed_roles:
        return False
    if role == 'gip':
        return _is_project_gip(user, document.project)
    if role in ('developer', 'contractor'):
        if document.created_by_id != user.pk:
            return False
        if not check_write_permission(user, document.section):
            return False

    if (
        new_suitability is not None
        and new_suitability != document.suitability_code
        and new_suitability not in SUITABILITY_TRANSITIONS.get(
            document.suitability_code, frozenset()
        )
    ):
        return False
    return True


def validate_transition(
    document: Document,
    new_status: str,
    user: User,
    new_suitability: str | None = None,
) -> None:
    """Выбрасывает ValidationError, если переход недопустим."""
    if new_status not in dict(Document.STATUS_CHOICES):
        raise ValidationError("Неизвестный статус документа")
    if (
        new_suitability is not None
        and new_suitability not in dict(Document.SUITABILITY_CHOICES)
    ):
        raise ValidationError("Неизвестный код пригодности документа")
    if can_transition(document, new_status, user, new_suitability):
        return

    if document.status == 'archived' and _user_role(user) != 'admin':
        raise ValidationError(
            "Нельзя выйти из архива без прав администратора"
        )
    if new_status == 'approved' and _has_open_issues(document):
        raise ValidationError(
            "Нельзя утвердить документ с открытыми замечаниями"
        )
    current_label = dict(Document.STATUS_CHOICES).get(
        document.status, document.status
    )
    target_label = dict(Document.STATUS_CHOICES).get(new_status, new_status)
    raise ValidationError(
        f"Переход из «{current_label}» в «{target_label}» запрещён"
    )


def update_document_status(
    user: User,
    document: Document,
    new_status: str,
    new_suitability: str | None = None,
) -> Document:
    """Единая точка смены статуса с FSM-валидацией."""
    validate_transition(document, new_status, user, new_suitability)
    update_fields = ['status', 'updated_at']
    document.status = new_status
    if (
        new_suitability is not None
        and new_suitability != document.suitability_code
    ):
        document.suitability_code = new_suitability
        update_fields.append('suitability_code')
    document._from_service = True
    document.save(update_fields=update_fields)
    return document


def _effective_zone(document: Document) -> str:
    """Зона документа; документы вне зон-папок считаются в WIP (черновик)."""
    folder = document.folder
    if folder is not None and _is_canonical_zone_folder(folder):
        return folder.zone
    return _ZONE_BY_STATUS.get(document.status, 'WIP')


def _zone_filter_q(zone: str) -> Q:
    """Условие фильтра по виртуальной зоне (системные зоны + статус)."""
    canonical = Folder.objects.filter(
        parent__isnull=True,
        section__isnull=True,
        is_system_zone=True,
    )
    return (
        Q(folder_id__in=canonical.filter(zone=zone).values('pk'))
        | (
            Q(status=ZONE_STATUS_MAP[zone])
            & (
                Q(folder__isnull=True)
                | ~Q(folder_id__in=canonical.values('pk'))
            )
        )
    )


def _is_project_gip(user: User, project: Project) -> bool:
    """ГИП проекта: прямой owner или членство с ролью gip."""
    if project.gip_id == user.id:
        return True
    return project.memberships.filter(
        user=user, role_in_project='gip'
    ).exists()


def _workflow_role_allowed(user: User, document: Document, action_code: str) -> bool:
    """Матрица ролей для контролируемого перехода зон."""
    if user.is_superuser:
        return True
    profile = getattr(user, 'userprofile', None)
    if profile is None:
        return False
    if not _check_company_access(user, document.project.company_id):
        return False
    action = ZONE_WORKFLOW_ACTIONS.get(action_code)
    if action is None or profile.role not in action['roles']:
        return False
    if profile.role == 'gip':
        return _is_project_gip(user, document.project)
    if profile.role in ('developer', 'contractor'):
        # подать может только автор работы в своём разделе
        if action_code in ('submit_review',):
            if document.created_by_id != user.id:
                return False
        return check_write_permission(user, document.section)
    if profile.role in ('checker', 'normocontrol'):
        # проверяющий действует только в проектах, где состоит
        # с осмысленной ролью (Спринт 5: наблюдатель не считается)
        return document.project.memberships.filter(user=user).exclude(
            role_in_project='viewer'
        ).exists()
    return True


OPEN_ISSUE_STATUSES = ('NEW', 'IN_PROGRESS', 'REJECTED')


def _open_issues_count(document: Document) -> int:
    annotated = getattr(document, 'open_issues_count', None)
    if annotated is not None:
        return annotated
    return Issue.objects.filter(
        document=document,
        status__in=OPEN_ISSUE_STATUSES,
    ).count()


def _has_open_issues(document: Document) -> bool:
    """Открытые замечания (NEW/IN_PROGRESS/REJECTED) блокируют публикацию."""
    annotated = getattr(document, 'open_issues_count', None)
    if annotated is not None:
        return annotated > 0
    return _open_issues_count(document) > 0


INTERNAL_ACTION_SPECS = {
    'send_to_review': ('internal_review', ('draft', 'rework'), 'send'),
    'return_to_rework': ('rework', ('internal_review',), 'review'),
    'approve_internal': ('ready_to_share', ('internal_review',), 'review'),
}


def _internal_permissions(user: User, document: Document):
    profile = getattr(user, 'userprofile', None)
    is_admin = user.is_superuser or (
        profile is not None and profile.role == 'admin'
    )
    is_author = document.created_by_id == user.id
    is_gip = (
        profile is not None
        and profile.role == 'gip'
        and _is_project_gip(user, document.project)
    )
    return is_admin, is_author, is_gip


def update_internal_status(
    user: User,
    document: Document,
    action: str,
) -> Document:
    """Выполняет внутреннее действие WIP-документа.

    Принимает только action-коды: send_to_review / return_to_rework /
    approve_internal / submit_review. Сырые значения internal_status
    не принимаются.
    """
    if document.is_deleted:
        raise ValueError("Документ находится в корзине")
    if _effective_zone(document) != 'WIP':
        raise ValueError("Внутренний статус доступен только в зоне WIP")

    action = (action or '').strip()
    if action == 'submit_review':
        return transition_document_zone(user, document, 'submit_review')

    is_admin, is_author, is_gip = _internal_permissions(user, document)
    spec = INTERNAL_ACTION_SPECS.get(action)
    if spec is None:
        raise ValueError("Неизвестное действие внутреннего статуса")

    new_status, from_statuses, mode = spec
    if document.internal_status not in from_statuses:
        raise ValueError("Действие недоступно для текущего внутреннего статуса")
    if mode == 'send':
        if not (is_admin or is_author or is_gip):
            raise PermissionDenied("Нет прав на отправку на проверку")
    elif not (is_admin or is_gip):
        raise PermissionDenied("Нет прав на проверку документа")

    if new_status == 'ready_to_share' and _has_open_issues(document):
        raise ValueError(
            "Нельзя утвердить документ с открытыми замечаниями"
        )

    document.internal_status = new_status
    update_fields = ['internal_status', 'updated_at']
    # Синхронизация старого поля status с внутренним WIP-статусом
    if document.status != 'draft':
        validate_transition(document, 'draft', user)
        document.status = 'draft'
        update_fields.append('status')
    document._from_service = True
    document.save(update_fields=update_fields)
    return document


def document_internal_actions(user: User, document: Document) -> list[dict]:
    """Кнопки внутреннего workflow для WIP-документа."""
    if (
        user is None
        or not user.is_authenticated
        or document.is_deleted
        or _effective_zone(document) != 'WIP'
    ):
        return []
    is_admin, is_author, is_gip = _internal_permissions(user, document)
    actions = []
    if (
        document.internal_status in ('draft', 'rework')
        and (is_admin or is_author or is_gip)
    ):
        actions.append({
            'code': 'send_to_review',
            'label': 'Отправить на проверку',
            'icon': 'bi-send',
            'kind': 'internal',
        })
    if document.internal_status == 'internal_review' and (is_admin or is_gip):
        actions.append({
            'code': 'return_to_rework',
            'label': 'Вернуть на доработку',
            'icon': 'bi-arrow-counterclockwise',
            'kind': 'internal',
        })
        if not _has_open_issues(document):
            actions.append({
                'code': 'approve_internal',
                'label': 'Согласовать',
                'icon': 'bi-check2',
                'kind': 'internal',
            })
    return actions


def document_actions(user: User, document: Document) -> list[dict]:
    """Все доступные действия: внутренние + зоны."""
    return (
        document_internal_actions(user, document)
        + document_workflow_actions(user, document)
    )


def document_workflow_actions(user: User, document: Document) -> list[dict]:
    """Доступные документу workflow-действия для отрисовки кнопок."""
    if user is None or not user.is_authenticated or document.is_deleted:
        return []
    current_zone = _effective_zone(document)
    actions = []
    for code, spec in ZONE_WORKFLOW_ACTIONS.items():
        if spec['from'] != current_zone:
            continue
        if code == 'submit_review' and (
            document.internal_status != 'ready_to_share'
            or _has_open_issues(document)
        ):
            continue
        if code in ('publish', 'approve') and _has_open_issues(document):
            continue
        target_status = ZONE_STATUS_MAP.get(spec['to'])
        target_suitability = ZONE_SUITABILITY_MAP.get(spec['to'])
        if not can_transition(
            document,
            target_status,
            user,
            target_suitability,
        ):
            continue
        if _workflow_role_allowed(user, document, code):
            actions.append({
                'code': code,
                'label': spec['label'],
                'icon': spec['icon'],
                'kind': 'zone',
            })
    return actions


def _get_or_create_zone_folder(project: Project, zone: str, user: User) -> Folder:
    """Каноническая папка-зона проекта; создаётся, если её снесли."""
    folder = Folder.objects.filter(
        project=project,
        zone=zone,
        parent=None,
        section=None,
        is_deleted=False,
    ).first()
    if folder is not None:
        return folder
    name = dict(Folder.ZONE_CHOICES)[zone]
    order = Folder.objects.filter(project=project).count() + 1
    return Folder.objects.create(
        project=project,
        name=name,
        zone=zone,
        parent=None,
        section=None,
        order=order,
        created_by=user,
    )


def transition_document_zone(
    user: User,
    document: Document,
    action_code: str,
) -> Document:
    """Контролируемый переход документа между зонами ISO 19650.

    Проверяет матрицу ролей, переносит документ в каноническую папку
    целевой зоны, синхронизирует статус документа и рассылает
    уведомления участникам (issues.Notification).
    """
    action = ZONE_WORKFLOW_ACTIONS.get(action_code)
    if action is None:
        raise ValueError("Неизвестное workflow-действие")
    if document.is_deleted:
        raise ValueError("Документ находится в корзине")
    if not _workflow_role_allowed(user, document, action_code):
        raise PermissionDenied(
            f"Нет прав на действие «{action['label']}»"
        )

    current_zone = _effective_zone(document)
    if current_zone != action['from']:
        raise ValueError(
            "Действие недоступно: документ находится в зоне «{}»".format(
                document.folder.get_zone_display()
                if (document.folder and _is_canonical_zone_folder(document.folder))
                else dict(Folder.ZONE_CHOICES).get(current_zone, current_zone)
            )
        )

    target_status = ZONE_STATUS_MAP.get(action['to'])
    target_suitability = ZONE_SUITABILITY_MAP.get(action['to'])
    validate_transition(
        document,
        target_status,
        user,
        target_suitability,
    )

    if action_code == 'submit_review':
        if document.internal_status != 'ready_to_share':
            raise ValueError(
                "Подать на согласование можно только после статуса "
                "«Готов к передаче»"
            )
        if _has_open_issues(document):
            raise ValueError(
                "Нельзя подать на согласование документ с открытыми замечаниями"
            )

    target_folder = document.folder
    if target_folder is None or _is_canonical_zone_folder(target_folder):
        target_folder = _get_or_create_zone_folder(
            document.project, action['to'], user
        )
    document.folder = target_folder
    if target_status is not None and target_status != document.status:
        document.status = target_status

    if target_suitability is not None:
        document.suitability_code = target_suitability
    if action_code == 'return_wip':
        document.internal_status = 'rework'

    document._from_service = True
    document.save(update_fields=[
        'folder', 'status', 'suitability_code', 'internal_status', 'updated_at',
    ])

    _notify_zone_transition(user, document, action_code)
    return document


def _notify_zone_transition(
    user: User,
    document: Document,
    action_code: str,
) -> None:
    """Уведомления о переходе зоны — точка интеграции с issues.notify."""
    try:
        from issues.notify import notify_document_workflow
    except Exception:  # pragma: no cover — уведомления не должны ронять workflow
        return
    notify_document_workflow(document, action_code, user)


def get_zone_counts(user: User, project: Project) -> dict:
    """Счётчики документов по зонам для сегмент-контрола."""
    _ensure_project_access(user, project)
    counts = {'WIP': 0, 'SHARED': 0, 'PUBLISHED': 0, 'ARCHIVE': 0}
    documents = (
        get_accessible_documents(user)
        .filter(project=project, is_deleted=False)
        .select_related('folder')
    )
    for document in documents:
        zone = _effective_zone(document)
        if zone in counts:
            counts[zone] += 1
    return {
        'total': sum(counts.values()),
        'WIP': counts['WIP'],
        'SHARED': counts['SHARED'],
        'PUBLISHED': counts['PUBLISHED'],
        'ARCHIVE': counts['ARCHIVE'],
    }


# Спринт «UI-аудит»: автоопределение типа документа по расширению файла
# (треб. 3.1): .dwg/.dxf/.pdf → чертёж; .doc/.docx/.xls/.xlsx → отчёт;
# .jpg/.png/.tif → другое. Расширения вне словаря → «other».
DOC_TYPE_BY_EXTENSION = {
    'dwg': 'drawing',
    'dxf': 'drawing',
    'pdf': 'drawing',
    'doc': 'report',
    'docx': 'report',
    'xls': 'report',
    'xlsx': 'report',
    'jpg': 'other',
    'jpeg': 'other',
    'png': 'other',
    'tif': 'other',
    'tiff': 'other',
}


def _detect_doc_type(filename: str) -> str:
    """Тип документа по расширению файла (треб. 3.1)."""
    extension = (
        filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    )
    return DOC_TYPE_BY_EXTENSION.get(extension, 'other')


def _generate_document_code(
    project: Project,
    section: Section,
    folder: Folder | None,
) -> str:
    """Шифр по ГОСТ Р 21.101 (треб. 2.1).

    Формат: [base_code]-[section.code]-[folder.code]-[порядковый номер].
    base_code берётся из проекта (при пустом — его шифр); составная часть
    папки добавляется только если у папки заполнен код. Порядковый номер —
    двузначный, продолжаем нумерацию внутри (проект, раздел) по префиксу,
    коллизии разрешаем перебором (уникальность в рамках project+section).
    """
    base = (project.base_code or project.code or '').strip()
    parts = [part for part in (
        base,
        (section.code or '').strip(),
        ((folder.code or '').strip() if folder is not None else ''),
    ) if part]
    prefix = '-'.join(parts) + '-' if parts else 'DOC-'
    existing_codes = set(
        Document.objects
        .filter(project=project, section=section, code__startswith=prefix)
        .values_list('code', flat=True)
    )
    number = len(existing_codes) + 1
    while f'{prefix}{number:02d}' in existing_codes:
        number += 1
    return f'{prefix}{number:02d}'


def upload_document(
    user: User,
    project: Project,
    section: Section,
    code: str = '',
    name: str = '',
    doc_type: str = '',
    file=None,
    comment: str = "",
    folder: Folder | None = None,
    skip_ai_index: bool = False,
) -> DocumentVersion:
    """Единственная точка создания версий документа.

    - Проверка прав через check_write_permission.
    - Проверка прав по зоне через check_zone_permission.
    - Проверка соответствия section.project == project.
    - Поиск/создание документа.
    - Автоинкремент номера версии.
    - Создание DocumentVersion с file_size и uploaded_by.
    - Обновление current_version и updated_at у документа.
    - Этап 20 (треб. 4): автоиндексация PDF-версии для ИИ-поиска
      (Celery-задача ai_checks.index_project_document) — когда включён
      AI_CHECKS_ENABLED и файл PDF. skip_ai_index=True отключает
      постановку задачи (документы базы знаний индексируются отдельно
      в InternalChunk через ai_checks).
    - UI-аудит (треб. 2.1/3.1): пустой code → генерация шифра по
      ГОСТ Р 21.101; пустой doc_type → определение по расширению файла.
    """
    if not check_write_permission(user, section):
        raise PermissionDenied("Нет прав на запись в этот раздел")

    # Спринт 5: валидация загружаемых файлов — расширение и размер.
    filename = getattr(file, 'name', '') or ''
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError(
            f"Формат «{extension or 'без расширения'}» не поддерживается. "
            "Разрешены: " + ', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        )
    file_size = getattr(file, 'size', 0) or 0
    if file_size <= 0:
        raise ValueError("Файл пустой")
    if file_size > MAX_UPLOAD_SIZE:
        raise ValueError(
            f"Файл больше {MAX_UPLOAD_SIZE // (1024 * 1024)} МБ — "
            "разбейте его или загрузите через обмен файлами"
        )

    if section.project != project:
        raise ValueError("Раздел не относится к указанному проекту")

    # Треб. 2.1: явный шифр от пользователя имеет приоритет; пустой —
    # генерируем по ГОСТ Р 21.101.
    code = (code or '').strip()
    if not code:
        code = _generate_document_code(project, section, folder)

    # Треб. 3.1: тип не указан — определяем по расширению файла.
    doc_type = (doc_type or '').strip()
    if not doc_type:
        doc_type = _detect_doc_type(filename)

    # Проверяем текущую зону документа (если документ уже существует и имеет папку)
    existing_doc = Document.objects.filter(
        project=project,
        section=section,
        code=code,
    ).select_related('folder').first()
    if existing_doc is not None:
        existing_zone = _effective_zone(existing_doc)
        if existing_zone == 'ARCHIVE':
            raise PermissionDenied(
                "Документ в архиве — загрузка новых версий запрещена"
            )
        if not check_zone_permission(
            user, existing_doc.folder, section, zone=existing_zone
        ):
            zone_name = (
                existing_doc.folder.get_zone_display()
                if existing_doc.folder is not None
                else dict(Folder.ZONE_CHOICES).get(existing_zone, existing_zone)
            )
            raise PermissionDenied(
                f"Нет прав на запись в текущую зону «{zone_name}»"
            )

    if folder is not None:
        if folder.project_id != project.pk:
            raise ValueError("Папка относится к другому проекту")
        if folder.is_deleted:
            raise ValueError("Нельзя загрузить файл в удалённую папку")
        if folder.section_id is not None and folder.section_id != section.pk:
            raise ValueError("Папка относится к другому разделу")

        # Проверка прав по целевой зоне
        if not check_zone_permission(user, folder, section):
            zone_name = folder.get_zone_display()
            raise PermissionDenied(
                f"Нет прав на запись в зону «{zone_name}»"
            )

    # Валидация doc_type
    if doc_type not in dict(Document.DOC_TYPE_CHOICES):
        raise ValueError("Неверный тип документа")

    # Блокируем транзакцией, чтобы избежать гонок версий и потерянных обновлений
    with transaction.atomic():
        # Получаем или создаём документ
        doc, created = Document.objects.select_for_update().get_or_create(
            project=project,
            section=section,
            code=code,
            defaults={
                'name': name,
                'doc_type': doc_type,
                'status': (
                    ZONE_STATUS_MAP.get(folder.zone, 'draft')
                    if folder is not None else 'draft'
                ),
                'suitability_code': (
                    ZONE_SUITABILITY_MAP.get(folder.zone, 'S0')
                    if folder is not None else 'S0'
                ),
                'folder': folder,
                'created_by': user,
            },
        )
        if not created:
            # Обновляем поля, если документ уже существует
            doc.name = name
            doc.doc_type = doc_type
            update_fields = ['name', 'doc_type', 'updated_at']
            if folder is not None:
                doc.folder = folder
                update_fields.append('folder')
                target_status = _status_for_zone(folder)
                target_suitability = _suitability_for_zone(folder)
                if target_status is not None and (
                    target_status != doc.status
                    or target_suitability != doc.suitability_code
                ):
                    validate_transition(
                        doc,
                        target_status,
                        user,
                        target_suitability,
                    )
                if (
                    (
                        _approval_target_changes(
                            doc,
                            target_status,
                            target_suitability,
                            folder.zone,
                        )
                        or folder.zone == 'PUBLISHED'
                    )
                    and _has_open_issues(doc)
                ):
                    raise ValueError(
                        "Нельзя обновить документ с открытыми замечаниями "
                        "в опубликованной зоне"
                    )
                if target_status is not None and target_status != doc.status:
                    doc.status = target_status
                    update_fields.append('status')
                if (
                    target_suitability is not None
                    and target_suitability != doc.suitability_code
                ):
                    doc.suitability_code = target_suitability
                    update_fields.append('suitability_code')
            doc._from_service = True
            doc.save(update_fields=update_fields)

        # Вычисляем следующую версию
        max_number = DocumentVersion.objects.filter(document=doc).aggregate(Max('version_number'))['version_number__max']
        next_version = (max_number or 0) + 1

        # Создаем DocumentVersion
        version = DocumentVersion.objects.create(
            document=doc,
            version_number=next_version,
            file=file,
            file_size=file.size,
            uploaded_by=user,
            comment=comment,
        )

        # Обновляем документ: текущая версия и отметка времени
        doc.current_version = version
        doc.updated_at = timezone.now()
        doc.save(update_fields=['current_version', 'updated_at'])

    # Этап 20 (треб. 4): после фиксации версии — автоиндексация PDF для
    # ИИ-поиска. Ленивый импорт задачи (домен storage не зависит от
    # ai_checks на уровне модуля); в dev/тестах Celery eager, в проде —
    # воркер. Сбой постановки не должен ронять загрузку документа.
    if (
        not skip_ai_index
        and getattr(settings, 'AI_CHECKS_ENABLED', False)
        and filename.lower().endswith('.pdf')
    ):
        try:
            from ai_checks.tasks import index_project_document_task

            index_project_document_task.delay(version.pk)
        except Exception:  # noqa: BLE001 — загрузка важнее индексации
            logger.exception(
                'Не удалось поставить задачу индексации версии %s',
                version.pk,
            )

    return version


def generate_vedomost(project: Project, section=None, *, company: Company, user: User | None = None) -> list:
    """Формирует список словарей для ведомости документов.

    Проект обязан принадлежать переданной компании — это защита от
    межтенантного доступа. Если передан user, документы дополнительно
    фильтруются через get_accessible_documents() с учётом роли
    (contractor видит только allowed_sections).
    """
    if project.company_id != company.pk:
        raise PermissionDenied("Нет прав на просмотр ведомости этого проекта")

    if user is not None:
        qs = get_accessible_documents(user)
    else:
        qs = Document.objects.filter(is_deleted=False)

    qs = qs.filter(project=project).select_related(
        'current_version',
        'current_version__uploaded_by',
        'created_by',
    ).order_by('section__order', 'code')

    if section is not None:
        qs = qs.filter(section=section)

    return [_document_row(doc) for doc in qs]


def get_document_versions(user: User, document: Document | int) -> QuerySet[DocumentVersion]:
    """Список версий документа, доступных на чтение.

    Проверка доступа — через check_read_permission(). current_version
    не изменяется. Для обратной совместимости принимает и pk документа.
    """
    if not isinstance(document, Document):
        document = Document.objects.get(pk=document)
    if not check_read_permission(user, document):
        raise PermissionDenied("Нет прав на чтение документа")
    return (
        document.versions.select_related('uploaded_by')
        .order_by('-version_number')
    )


def get_document_version_for_view(
    user: User,
    document: Document,
    version_id: int | None,
) -> DocumentVersion:
    """Возвращает версию документа для просмотра с проверкой доступа.

    version_id is None → document.current_version.
    version_id задан → версия обязана принадлежать ЭТОМУ документу
    (защита от IDOR), иначе DocumentVersion.DoesNotExist.
    ValueError возникает только при version_id is None и отсутствии
    current_version; при переданном version_id отсутствующая/чужая
    версия даёт DocumentVersion.DoesNotExist.
    current_version не изменяется.
    """
    if not check_read_permission(user, document):
        raise PermissionDenied("Нет прав на чтение документа")

    if version_id is None:
        version = document.current_version
        if version is None:
            raise ValueError("У документа нет текущей версии")
        return version

    try:
        return document.versions.select_related('uploaded_by').get(pk=version_id)
    except DocumentVersion.DoesNotExist:
        raise DocumentVersion.DoesNotExist(
            "Версия не принадлежит указанному документу"
        ) from None


def search_documents(
    user: User,
    project: Project,
    query: str = "",
    section: Section | None = None,
    doc_type: str | None = None,
    status: str | None = None,
) -> list[Document]:
    """Поиск документов проекта по метаданным с учётом прав пользователя.

    Ищет по шифру, названию и комментариям версий. Пустой query не сужает
    выборку: применяются только переданные фильтры.
    """
    queryset = get_accessible_documents(user).filter(project=project)

    query = (query or "").strip()
    if query:
        queryset = queryset.filter(
            Q(code__icontains=query)
            | Q(name__icontains=query)
            | Q(versions__comment__icontains=query)
        ).distinct()

    if section is not None:
        queryset = queryset.filter(section=section)
    if doc_type:
        queryset = queryset.filter(doc_type=doc_type)
    if status:
        queryset = queryset.filter(status=status)

    return list(
        queryset.select_related(
            'section',
            'current_version',
            'current_version__uploaded_by',
        )
        .prefetch_related(
            Prefetch(
                'versions',
                queryset=DocumentVersion.objects.order_by('-version_number'),
            )
        )
        .order_by('section__order', 'code')
    )


ISSUE_DEVELOPER_STATUSES = ('IN_PROGRESS', 'DISCUSSION', 'DONE')
ISSUE_REVIEWER_STATUSES = ('REJECTED', 'CLOSED', 'CANCELLED')
ISSUE_CLOSED_STATUSES = ('CLOSED', 'CANCELLED')
ISSUE_CREATOR_ROLES = (
    'admin',
    'gip',
    'developer',
    'checker',
    'normocontrol',
    'customer',
    'contractor',
)


def check_issue_permission(user: User, document: Document) -> bool:
    """Право на создание замечания: все ГОСТ-роли, кроме viewer."""
    if user.is_superuser:
        return True
    if not check_read_permission(user, document):
        return False

    profile = getattr(user, 'userprofile', None)
    if profile is None or profile.role not in ISSUE_CREATOR_ROLES:
        return False
    if profile.role == 'contractor':
        return document.section in profile.allowed_sections.all()
    return True


def allowed_issue_statuses(user: User, issue: Issue) -> list[str]:
    """Статусы, которые пользователь может выставить по матрице ролей."""
    if not check_read_permission(user, issue.document):
        return []

    all_statuses = [code for code, _ in Issue.STATUS_CHOICES]
    if user.is_superuser:
        return all_statuses

    profile = getattr(user, 'userprofile', None)
    if profile is None:
        return []

    if (
        issue.status in ISSUE_CLOSED_STATUSES
        and profile.role not in ('admin', 'gip')
    ):
        return []

    if profile.role in ('admin', 'gip'):
        return all_statuses
    if profile.role == 'developer':
        return [code for code in all_statuses if code in ISSUE_DEVELOPER_STATUSES]
    if profile.role in ('checker', 'normocontrol', 'customer'):
        return [code for code in all_statuses if code in ISSUE_REVIEWER_STATUSES]
    return []


def update_issue_status(user: User, issue: Issue, new_status: str) -> Issue:
    """Меняет статус замечания по матрице ГОСТ-ролей."""
    if new_status not in dict(Issue.STATUS_CHOICES):
        raise ValueError("Неверный статус замечания")

    allowed = allowed_issue_statuses(user, issue)
    if new_status not in allowed:
        raise PermissionDenied("Нет прав на установку этого статуса")

    if new_status == issue.status:
        return issue

    profile = getattr(user, 'userprofile', None)
    role = profile.role if profile is not None else None
    current_status = issue.status

    if (
        current_status in ISSUE_CLOSED_STATUSES
        and not user.is_superuser
        and role not in ('admin', 'gip')
    ):
        raise PermissionDenied(
            "Закрытое или аннулированное замечание может изменить только ГИП или администратор"
        )

    if (
        current_status == 'DONE'
        and role == 'developer'
        and new_status not in ('DISCUSSION', 'IN_PROGRESS')
    ):
        raise PermissionDenied(
            "Из статуса «Выполнено» разработчик может перевести замечание "
            "только в «К обсуждению» или «Принято в работу»"
        )

    issue.status = new_status
    update_fields = ['status', 'updated_at']

    if new_status in ISSUE_CLOSED_STATUSES:
        issue.closed_at = timezone.now()
        issue.closed_by = user
        issue.actual_fix_date = timezone.localdate()
        update_fields.extend(
            ['closed_at', 'closed_by', 'actual_fix_date']
        )
    else:
        issue.closed_at = None
        issue.closed_by = None
        issue.actual_fix_date = None
        update_fields.extend(
            ['closed_at', 'closed_by', 'actual_fix_date']
        )

    if new_status == 'IN_PROGRESS' and issue.assignee_id is None:
        issue.assignee = user
        update_fields.append('assignee')

    issue.save(update_fields=update_fields)
    notify_issue_status_changed(issue, user, current_status)
    return issue


def get_issues_for_version(
    document: Document,
    version: DocumentVersion | None = None,
):
    """Замечания документа; при указании версии — только для неё."""
    queryset = document.issues.select_related('author', 'assignee', 'version')
    if version is not None:
        queryset = queryset.filter(version=version)
    return queryset.order_by('-created_at')


def get_issue_assignees(user: User, document: Document):
    """Разработчики проекта, которых можно назначить ответственным."""
    if not check_read_permission(user, document):
        return User.objects.none()
    return (
        User.objects.filter(
            projectmembership__project=document.project,
            projectmembership__role_in_project='developer',
            is_active=True,
        )
        .distinct()
        .order_by('last_name', 'username')
    )


def _decode_crop_image(crop_image_base64: str | None):
    if not crop_image_base64:
        return None
    payload = crop_image_base64
    if ',' in payload:
        payload = payload.split(',', 1)[1]
    try:
        decoded = base64.b64decode(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректное изображение маркапа") from exc
    if not decoded:
        raise ValueError("Пустое изображение маркапа")
    if len(decoded) > 5 * 1024 * 1024:
        raise ValueError("Изображение маркапа слишком большое")
    if not decoded.startswith(b'\xff\xd8'):
        raise ValueError("Маркап должен быть JPEG-изображением")
    return ContentFile(decoded, name=f'issue_crop_{uuid.uuid4().hex}.jpg')


def create_issue(
    user: User,
    document: Document,
    description: str,
    version: DocumentVersion | None = None,
    page_number: int | None = None,
    x_ratio: float | None = None,
    y_ratio: float | None = None,
    assignee: User | None = None,
    x1_ratio: float | None = None,
    y1_ratio: float | None = None,
    x2_ratio: float | None = None,
    y2_ratio: float | None = None,
    crop_image_base64: str | None = None,
    department: str = '',
    project_organization: str = '',
    answer_text: str = '',
    due_date=None,
    revision: str = '',
) -> Issue:
    """Создаёт замечание к документу с проверкой прав и валидацией координат."""
    if not check_issue_permission(user, document):
        raise PermissionDenied("Нет прав на добавление замечания к документу")

    markup = (x1_ratio, y1_ratio, x2_ratio, y2_ratio)
    has_markup = any(value is not None for value in markup)
    if has_markup:
        if any(value is None for value in markup):
            raise ValueError("Для маркапа нужны все четыре координаты")
        for value in markup:
            if not (0.0 <= value <= 1.0):
                raise ValueError("Координаты маркапа должны быть в диапазоне 0.0 — 1.0")
        if x1_ratio >= x2_ratio or y1_ratio >= y2_ratio:
            raise ValueError("Координаты маркапа заданы некорректно")
        if page_number is None:
            raise ValueError("Для маркапа нужен номер страницы")
        if x_ratio is None:
            x_ratio = (x1_ratio + x2_ratio) / 2
        if y_ratio is None:
            y_ratio = (y1_ratio + y2_ratio) / 2

    if page_number is None and (x_ratio is not None or y_ratio is not None):
        raise ValueError("x_ratio и y_ratio требуют page_number")

    if page_number is not None:
        if page_number < 1:
            raise ValueError("Номер страницы должен быть больше 0")
        if x_ratio is None or y_ratio is None:
            raise ValueError(
                "При указании page_number необходимо указать x_ratio и y_ratio"
            )
    if x_ratio is not None and not (0.0 <= x_ratio <= 1.0):
        raise ValueError("x_ratio должен быть в диапазоне 0.0 — 1.0")
    if y_ratio is not None and not (0.0 <= y_ratio <= 1.0):
        raise ValueError("y_ratio должен быть в диапазоне 0.0 — 1.0")

    if version is not None and version.document_id != document.pk:
        raise ValueError("Указанная версия не принадлежит данному документу")

    if assignee is not None:
        if not document.project.memberships.filter(
            user=assignee, role_in_project='developer'
        ).exists():
            raise ValueError("Ответственный должен быть разработчиком проекта")

    crop_image = _decode_crop_image(crop_image_base64)

    issue = Issue.objects.create(
        document=document,
        version=version,
        author=user,
        assignee=assignee,
        description=description,
        status='NEW',
        page_number=page_number,
        x_ratio=x_ratio,
        y_ratio=y_ratio,
        x1_ratio=x1_ratio,
        y1_ratio=y1_ratio,
        x2_ratio=x2_ratio,
        y2_ratio=y2_ratio,
        crop_image=crop_image,
        department=department,
        project_organization=project_organization,
        answer_text=answer_text,
        due_date=due_date,
        revision=revision,
    )
    notify_issue_assigned(issue, user)
    return issue


def update_issue_answer(user: User, issue: Issue, answer_text: str) -> Issue:
    """Сохраняет текст ответа на замечание.

    Спринт 5: право ограничено — исполнитель (assignee), ГИП проекта,
    администратор компании или superuser. Роли «только чтение» (viewer,
    customer, checker) больше не могут перезаписывать официальные ответы.
    """
    if not check_read_permission(user, issue.document):
        raise PermissionDenied("Нет доступа к документу")
    if not _can_edit_issue_answer(user, issue):
        raise PermissionDenied("Нет прав на изменение ответа на замечание")
    issue.answer_text = answer_text
    issue.save(update_fields=['answer_text', 'updated_at'])
    return issue


def _can_edit_issue_answer(user: User, issue: Issue) -> bool:
    """Матрица прав на редактирование официального ответа.

    Отвечающая сторона: исполнитель (assignee) и разработчики проекта,
    плюс ГИП проекта и администратор компании. Наблюдатель, заказчик и
    проверяющий — только чтение.
    """
    if user.is_superuser:
        return True
    profile = getattr(user, 'userprofile', None)
    if profile is not None and profile.role == 'admin':
        return True
    if _is_project_gip(user, issue.document.project):
        return True
    if issue.assignee_id == user.pk:
        return True
    if profile is not None and profile.role == 'developer':
        return issue.document.project.memberships.filter(
            user=user, role_in_project__in=('developer', 'gip'),
        ).exists()
    return False


def delete_issue(user: User, issue: Issue) -> None:
    """Удаляет замечание: автор, администратор или суперпользователь."""
    profile = getattr(user, 'userprofile', None)
    if not (
        user.is_superuser
        or issue.author_id == user.pk
        or (profile is not None and profile.role == 'admin')
    ):
        raise PermissionDenied("Нет прав на удаление замечания")
    issue.delete()


# Цвета рамок замечаний в экспорте PDF приходят из дизайн-токенов:
# static/design-tokens/tokens.json -> scripts/build_design_tokens.py -> design_colors.py.
# Правится в tokens.json, не здесь.


def export_pdf_with_issues(user: User, document: Document) -> bytes:
    """Возвращает PDF текущей версии с наложенными маркапами замечаний.

    Реализация (замена PyMuPDF, лицензия AGPL): оверлей reportlab (BSD)
    с рамками/подписями замечаний + слияние на страницы pypdf (BSD).
    Координаты маркапов — неповёрнутая система PyMuPDF (TL-начало,
    как page.rect/get_drawings); в пользовательское пространство PDF
    конвертируются без учёта /Rotate — ровно как рисовало PyMuPDF
    (проверено зондом: draw_rect/insert_text пишут контент в потоке
    неповёрнутым даже на повернутых листах).
    """
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas as rl_canvas

    if not check_read_permission(user, document):
        raise PermissionDenied('Нет прав на скачивание документа')

    version = document.current_version
    if version is None or not version.file:
        raise ValueError('У документа нет текущей версии')
    if not version.file.storage.exists(version.file.name):
        raise ValueError('Файл текущей версии не найден')

    issues = document.issues.filter(page_number__isnull=False).order_by('pk')

    with open(version.file.path, 'rb') as source_handle:
        reader = PdfReader(source_handle)
        writer = PdfWriter(clone_from=reader)

        markers: dict[int, list[Issue]] = {}
        for issue in issues:
            page_index = issue.page_number - 1
            if page_index < 0 or page_index >= len(reader.pages):
                continue
            markers.setdefault(page_index, []).append(issue)

        buffer = io.BytesIO()
        if markers:
            # Оверлей: страница на каждую страницу исходника — сохраняем
            # соответствие индексов при слиянии.
            overlay_buffer = io.BytesIO()
            canvas = rl_canvas.Canvas(overlay_buffer)
            for page_index, base_page in enumerate(reader.pages):
                cropbox = base_page.cropbox
                width = float(cropbox.width)
                height = float(cropbox.height)
                canvas.setPageSize((width, height))
                # Размеры «display» — как page.rect в PyMuPDF (с учётом
                # /Rotate); сами маркапы при этом рисуются в неповёрнутом
                # потоке (семантика PyMuPDF, проверена зондом).
                rotation = int(base_page.get('/Rotate', 0) or 0) % 360
                if rotation in (90, 270):
                    display_w, display_h = height, width
                else:
                    display_w, display_h = width, height
                for issue in markers.get(page_index, []):
                    color = PDF_ISSUE_COLORS.get(
                        issue.status, PDF_ISSUE_COLORS['NEW'],
                    )
                    _draw_issue_marker(
                        rl_canvas, canvas,
                        float(cropbox.left), float(cropbox.bottom),
                        float(cropbox.right), float(cropbox.top),
                        issue, Color(*color), display_w, display_h,
                    )
                canvas.showPage()
            canvas.save()

            overlay_buffer.seek(0)
            overlay_reader = PdfReader(overlay_buffer)
            for page_index in markers:
                writer.pages[page_index].merge_page(
                    overlay_reader.pages[page_index],
                )

        writer.write(buffer)
        return buffer.getvalue()


def _display_to_user(left, bottom, right, top, x_tl, y_tl):
    """Точка в неповёрнутой TL-системе PyMuPDF -> пользовательское (BL).

    Поворот листа (/Rotate) НЕ учитывается — PyMuPDF пишет контент
    в поток неповёрнутым (проверено зондом на rot 90/180/270).
    """
    return left + x_tl, top - y_tl


def _draw_issue_marker(
    rl_canvas, canvas, left, bottom, right, top,
    issue: Issue, color, display_width, display_height,
):
    """Рисует маркап замечания на оверлей-странице reportlab.

    Эквивалент прежней PyMuPDF-отрисовки: draw_rect/draw_circle +
    insert_text подписи '#<pk>' (шрифт Helvetica 8, как base14 'helv').
    """
    canvas.setStrokeColor(color)
    canvas.setFillColor(color)
    canvas.setLineWidth(2)

    def to_user(x_d, y_d):
        return _display_to_user(left, bottom, right, top, x_d, y_d)

    if (
        issue.x1_ratio is not None and issue.y1_ratio is not None
        and issue.x2_ratio is not None and issue.y2_ratio is not None
    ):
        x1_d = display_width * min(issue.x1_ratio, issue.x2_ratio)
        y1_d = display_height * min(issue.y1_ratio, issue.y2_ratio)
        x2_d = display_width * max(issue.x1_ratio, issue.x2_ratio)
        y2_d = display_height * max(issue.y1_ratio, issue.y2_ratio)
        ux1, uy1 = to_user(x1_d, y1_d)
        ux2, uy2 = to_user(x2_d, y2_d)
        canvas.rect(min(ux1, ux2), min(uy1, uy2),
                    abs(ux2 - ux1), abs(uy2 - uy1), stroke=1, fill=0)
        label_x, label_y = to_user(x1_d + 3, max(y1_d - 3, 8))
    elif issue.x_ratio is not None and issue.y_ratio is not None:
        cx_d = display_width * issue.x_ratio
        cy_d = display_height * issue.y_ratio
        cx, cy = to_user(cx_d, cy_d)
        canvas.circle(cx, cy, 9, stroke=1, fill=0)
        label_x, label_y = to_user(cx_d + 12, max(cy_d - 12, 8))
    else:
        return

    canvas.setFont('Helvetica', 8)
    canvas.drawString(label_x, label_y, f'#{issue.pk}')


def add_issue_comment(user: User, issue: Issue, text: str):
    """Добавляет комментарий к замечанию с проверкой доступа и роли."""
    if not check_read_permission(user, issue.document):
        raise PermissionDenied('Нет доступа к этому замечанию')

    if not user.is_superuser:
        profile = getattr(user, 'userprofile', None)
        if profile is None or profile.role == 'viewer':
            raise PermissionDenied('Наблюдатели не могут комментировать')

    text = (text or '').strip()
    if not text:
        return None
    comment = IssueComment.objects.create(
        issue=issue,
        author=user,
        text=text,
    )
    notify_issue_commented(issue, user)
    return comment


def delete_issue_crop(user: User, issue: Issue) -> Issue:
    """Удаляет прикреплённый маркап замечания с проверкой прав."""
    if not check_read_permission(user, issue.document):
        raise PermissionDenied('Нет доступа')

    profile = getattr(user, 'userprofile', None)
    is_admin = profile is not None and profile.role == 'admin'
    if not (
        user.is_superuser
        or issue.author_id == user.pk
        or is_admin
    ):
        raise PermissionDenied('Нет прав на удаление маркапа')

    if issue.crop_image:
        issue.crop_image.delete(save=False)
        issue.crop_image = None
        issue.save(update_fields=['crop_image'])
    return issue


@transaction.atomic
def create_project_from_template(
    company: Company,
    code: str,
    name: str,
    customer: str,
    gip: User | None,
    created_by: User | None,
    template_key: str | None = None,
    base_code: str = '',
) -> Project:
    """Создаёт проект и, при необходимости, разделы/папки из шаблона."""
    actor = created_by or gip
    if actor is None:
        raise ValueError("Не указан создатель проекта")
    if not can_create_project(actor):
        raise PermissionDenied("Нет прав на создание проекта")

    project = create_project(
        company=company,
        code=code,
        name=name,
        customer=customer,
        gip=gip,
        created_by=created_by,
        base_code=base_code,
    )
    stored_template_key = template_key or ''
    if project.template_key != stored_template_key:
        project.template_key = stored_template_key
        project.save(update_fields=['template_key'])
    if gip is not None:
        ProjectMembership.objects.get_or_create(
            project=project,
            user=gip,
            defaults={'role_in_project': 'gip'},
        )
    if getattr(project, '_restored_from_trash', False):
        # Старый проект восстановлен вместе с существующей структурой;
        # повторно шаблон не разворачиваем, чтобы не создавать дубликаты.
        return project
    template = PROJECT_TEMPLATES.get(template_key)

    # Легаси-шаблон: сохраняем прежнюю структуру разделов/папок.
    if template_key == 'residential' and template is not None:
        sections = {}
        for index, section_data in enumerate(template.get('sections', []), start=1):
            section = create_section(
                project,
                section_data['code'],
                section_data['name'],
                index,
                user=actor,
                force=True,
            )
            sections[section.code] = section

        for folder_data in template.get('folders', []):
            section = sections.get(folder_data.get('section_code'))
            if section is None:
                continue
            create_folder(
                actor,
                project,
                folder_data['name'],
                section=section,
                code=folder_data.get('code', ''),
                force=True,
            )

        return project

    # Любой новый проект получает 4 корневые суперпапки.
    if template is None:
        _ensure_root_superfolders(project, actor)
        return project

    _populate_project_template(project, template, actor)
    return project


def get_company_members(company: Company):
    """Все профили пользователей компании."""
    return UserProfile.objects.filter(company=company).select_related('user')


def _is_company_admin(actor: User) -> bool:
    if actor.is_superuser:
        return True
    # Сброс кэша user.userprofile: к этому моменту роль актёра могла быть
    # изменена (update_or_create в Панели управления кэширует старый профиль).
    _clear_profile_cache(actor)
    profile = getattr(actor, 'userprofile', None)
    return profile is not None and profile.role == 'admin'


def add_company_member(
    actor: User,
    company: Company,
    user: User,
    role: str,
    allowed_sections=None,
) -> UserProfile:
    """Добавляет пользователя в компанию или меняет его роль."""
    valid_roles = {code for code, _ in UserProfile.ROLE_CHOICES}
    if role not in valid_roles:
        raise ValueError('Неверная роль пользователя')

    # Сброс кэша user.userprofile у актёра: роль могла быть обновлена
    # через queryset, который закэшировал устаревший профиль.
    _clear_profile_cache(actor)

    # Хардендинг: админ ЭТОЙ компании, а не любой (superuser — вне
    # ограничений). Вьюхи это проверяют, сервис — защищает от прямых вызовов.
    if not actor.is_superuser:
        actor_profile = getattr(actor, 'userprofile', None)
        if (
            actor_profile is None
            or actor_profile.company_id != company.pk
        ):
            raise PermissionDenied(
                'Управлять участниками можно только своей компании'
            )

    if role == 'admin' and not _is_company_admin(actor):
        raise PermissionDenied(
            'Только администратор или суперюзер может назначать администраторов'
        )

    profile = UserProfile.objects.filter(user=user).first()
    if (
        profile is not None
        and profile.company_id is not None
        and profile.company_id != company.pk
    ):
        raise PermissionDenied(
            'Пользователь уже принадлежит другой компании'
        )

    if (
        profile is not None
        and profile.role == 'admin'
        and not _is_company_admin(actor)
    ):
        raise PermissionDenied(
            'Только администратор или суперюзер может изменять администраторов'
        )

    if allowed_sections is not None:
        section_ids = [
            getattr(section, 'pk', section)
            for section in allowed_sections
        ]
        unique_ids = set(section_ids)
        sections = Section.objects.filter(pk__in=unique_ids)
        if sections.count() != len(unique_ids):
            raise ValueError('Один или несколько разделов не найдены')
        if sections.exclude(project__company=company).exists():
            raise ValueError(
                'Разделы должны принадлежать компании пользователя'
            )
    else:
        section_ids = None

    with transaction.atomic():
        if profile is None:
            profile = UserProfile(user=user)

        profile.company = company
        profile.role = role
        profile.save()

        if section_ids is not None:
            if role in ('developer', 'contractor'):
                profile.allowed_sections.set(section_ids)
            else:
                profile.allowed_sections.clear()

    _clear_profile_cache(user)
    return profile


def remove_company_member(actor: User, company: Company, user: User) -> None:
    """Убирает пользователя из компании, сохраняя его профиль."""
    _clear_profile_cache(actor)
    if not actor.is_superuser:
        actor_profile = getattr(actor, 'userprofile', None)
        if (
            actor_profile is None
            or actor_profile.company_id != company.pk
        ):
            raise PermissionDenied(
                'Управлять участниками можно только своей компании'
            )

    target_profile = UserProfile.objects.filter(
        company=company, user=user
    ).first()
    if target_profile is None:
        return

    if target_profile.role == 'admin' and not _is_company_admin(actor):
        raise PermissionDenied(
            'Только администратор или суперюзер может удалять администраторов'
        )

    target_profile.company = None
    target_profile.save(update_fields=['company'])
    _clear_profile_cache(user)


def update_company_info(
    company: Company,
    name: str | None = None,
    logo=None,
    is_private: bool | None = None,
) -> Company:
    """Обновляет название, логотип и приватность компании."""
    if name is not None:
        company.name = name.strip()
    if logo is not None:
        company.logo = logo
    if is_private is not None:
        company.is_private = is_private
    company.save()
    return company


FEEDBACK_SCREENSHOT_MAX_SIZE = 10 * 1024 * 1024
FEEDBACK_SCREENSHOT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
FEEDBACK_SCREENSHOT_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
FEEDBACK_ADMIN_COMMENT_MAX_LENGTH = 2000


def _resolve_feedback_company(user, company):
    """Определяет компанию для обратной связи.

    Superuser без профиля/компании привязывается к первой компании или
    к новой «Stream-CAD». Обычный пользователь без компании получает
    понятную ValidationError.
    """
    profile = getattr(user, "userprofile", None)

    if user.is_superuser:
        if company is None:
            if profile is not None and profile.company_id is not None:
                company = profile.company
            else:
                company = Company.objects.order_by("pk").first()
                if company is None:
                    company, _ = Company.objects.get_or_create(
                        name="Stream-CAD"
                    )

        if profile is None:
            UserProfile.objects.create(
                user=user,
                company=company,
                role="admin",
            )
        elif profile.company_id is None:
            profile.company = company
            profile.save(update_fields=["company"])
        elif profile.company_id != company.pk:
            raise PermissionDenied("Пользователь не привязан к этой компании")

        return company

    if profile is None or profile.company_id is None:
        raise ValidationError(
            "У вас не указана компания. Обратитесь к администратору."
        )
    if company is None:
        return profile.company
    if profile.company_id != company.pk:
        raise PermissionDenied("Пользователь не привязан к этой компании")
    return company


def submit_feedback(
    user,
    company,
    message: str,
    category: str = "BUG",
    page_url: str = "",
    contact_email: str = "",
    screenshot=None,
    file_storage=None,
) -> Feedback:
    """Создаёт запись обратной связи от пользователя.

    Скриншот сохраняется через FileStorage-порт: адаптер передаёт view,
    по умолчанию используется Django default_storage.
    """
    if user is None or not user.is_authenticated:
        raise PermissionDenied("Отправить обратную связь может только пользователь")

    company = _resolve_feedback_company(user, company)

    message = (message or "").strip()
    if not message:
        raise ValidationError({"message": "Сообщение не может быть пустым"})
    if len(message) > 5000:
        raise ValidationError(
            {"message": "Сообщение не может быть длиннее 5000 символов"}
        )

    category = category or Feedback.Category.BUG
    if category not in Feedback.Category.values:
        raise ValidationError({"category": "Неверная категория обратной связи"})

    page_url = (page_url or "").strip()
    if len(page_url) > 500:
        raise ValidationError(
            {"page_url": "URL страницы не может быть длиннее 500 символов"}
        )

    contact_email = (contact_email or "").strip() or (user.email or "").strip()
    if contact_email:
        try:
            validate_email(contact_email)
        except ValidationError as exc:
            raise ValidationError({"contact_email": exc.messages}) from exc

    feedback = Feedback(
        company=company,
        author=user,
        category=category,
        message=message,
        page_url=page_url,
        contact_email=contact_email,
    )

    saved_path = None
    if screenshot is not None:
        size = getattr(screenshot, "size", 0) or 0
        if size > FEEDBACK_SCREENSHOT_MAX_SIZE:
            raise ValidationError(
                {"screenshot": "Скриншот не должен превышать 10 МБ"}
            )

        content_type = (
            getattr(screenshot, "content_type", "") or ""
        ).split(";", 1)[0].strip().lower()
        original_name = getattr(screenshot, "name", "") or ""
        original_ext = (
            original_name.rsplit(".", 1)[-1].lower()
            if "." in original_name
            else ""
        )

        if content_type:
            extension = FEEDBACK_SCREENSHOT_TYPES.get(content_type)
        else:
            extension = original_ext
            if extension not in FEEDBACK_SCREENSHOT_EXTENSIONS:
                extension = None

        if extension is None:
            raise ValidationError(
                {"screenshot": "Допустимы только JPEG, PNG, WebP"}
            )
        if extension == "jpeg":
            extension = "jpg"

        # Спринт 7: content_type приходит от клиента и подделывается —
        # проверяем, что байты действительно являются изображением.
        try:
            screenshot.open("rb")
            screenshot.seek(0)
        except (AttributeError, OSError):
            pass
        try:
            with Image.open(screenshot) as _img:
                _img.verify()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ValidationError(
                {"screenshot": "Файл не является корректным изображением"}
            ) from exc
        finally:
            try:
                screenshot.seek(0)
            except (AttributeError, OSError):
                pass

        storage = file_storage or default_storage
        upload_to = Feedback._meta.get_field("screenshot").upload_to
        if isinstance(upload_to, str):
            folder = timezone.now().strftime(upload_to)
        else:
            folder = f"feedback/screenshots/{timezone.now():%Y/%m}/"
        path = f"{folder}{uuid.uuid4().hex}.{extension}"
        saved_path = storage.save(path, screenshot)
        feedback.screenshot.name = saved_path
        feedback.screenshot._committed = True

    try:
        feedback.save()
    except Exception:
        if saved_path is not None:
            storage.delete(saved_path)
        raise

    return feedback


def list_feedback(
    user,
    company,
    status: str | None = None,
    category: str | None = None,
):
    """Возвращает обратную связь компании для администратора или superuser."""
    if user is None or not user.is_authenticated:
        raise PermissionDenied("Нет доступа к обратной связи")

    if not user.is_superuser:
        profile = getattr(user, "userprofile", None)
        if (
            company is None
            or profile is None
            or profile.role != "admin"
            or profile.company_id != company.pk
        ):
            raise PermissionDenied("Нет доступа к обратной связи")

    if company is None:
        raise PermissionDenied("Компания не указана")

    queryset = (
        Feedback.objects.filter(company=company)
        .select_related("author")
        .order_by("-created_at")
    )

    if status is not None:
        if status not in Feedback.Status.values:
            raise ValidationError({"status": "Неверный статус обратной связи"})
        queryset = queryset.filter(status=status)

    if category is not None:
        if category not in Feedback.Category.values:
            raise ValidationError({"category": "Неверная категория обратной связи"})
        queryset = queryset.filter(category=category)

    return queryset


def update_feedback_status(
    user,
    feedback,
    new_status: str,
    admin_comment: str = "",
) -> Feedback:
    """Меняет статус обратной связи и, при необходимости, комментарий админа."""
    if user is None or not user.is_authenticated:
        raise PermissionDenied("Нет доступа к обратной связи")

    if not user.is_superuser:
        profile = getattr(user, "userprofile", None)
        if (
            profile is None
            or profile.role != "admin"
            or profile.company_id != feedback.company_id
        ):
            raise PermissionDenied("Нет доступа к обратной связи")

    if new_status not in Feedback.Status.values:
        raise ValidationError({"status": "Неверный статус обратной связи"})

    comment = (admin_comment or "").strip()
    if len(comment) > FEEDBACK_ADMIN_COMMENT_MAX_LENGTH:
        raise ValidationError(
            {
                "admin_comment": (
                    "Комментарий не может быть длиннее "
                    f"{FEEDBACK_ADMIN_COMMENT_MAX_LENGTH} символов"
                )
            }
        )

    feedback.status = new_status
    update_fields = ["status", "updated_at"]
    if comment:
        feedback.admin_comment = comment
        update_fields.append("admin_comment")
    feedback.save(update_fields=update_fields)
    return feedback


def _check_api_token_admin(user: User, company: Company | None) -> None:
    """Проверяет право управлять API-токенами компании."""
    if user is None or not user.is_authenticated:
        raise PermissionDenied("Нет доступа к API-токенам")
    if company is None:
        raise PermissionDenied("Компания не указана")
    if user.is_superuser:
        return
    profile = getattr(user, "userprofile", None)
    if (
        profile is None
        or profile.role != "admin"
        or profile.company_id != company.pk
    ):
        raise PermissionDenied("Нет доступа к API-токенам")


def create_api_token(
    user: User,
    company: Company,
    name: str,
    scopes: list[str],
    expires_at=None,
):
    """Создаёт API-токен. Возвращает (ApiToken, raw_token).

    raw_token показывается пользователю ОДИН РАЗ, в БД хранится только хэш.
    Доступ: admin компании / superuser. scopes — валидные значения ApiToken.Scope.
    """
    import hashlib
    import secrets

    from external_api.models import ApiToken

    _check_api_token_admin(user, company)

    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Название токена обязательно"})
    if len(name) > 100:
        raise ValidationError(
            {"name": "Название токена не может быть длиннее 100 символов"}
        )

    if isinstance(scopes, str):
        scopes = [scopes]
    normalized_scopes = []
    for scope in scopes or []:
        scope = (scope or "").strip()
        if scope and scope not in normalized_scopes:
            normalized_scopes.append(scope)
    if not normalized_scopes:
        raise ValidationError({"scopes": "Выберите хотя бы один scope"})
    invalid = [
        scope
        for scope in normalized_scopes
        if scope not in ApiToken.Scope.values
    ]
    if invalid:
        raise ValidationError({"scopes": "Неверные scopes"})

    if expires_at is not None:
        if timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(expires_at)
        if expires_at <= timezone.now():
            raise ValidationError(
                {"expires_at": "Срок действия должен быть в будущем"}
            )

    raw_token = secrets.token_urlsafe(48)
    token = ApiToken.objects.create(
        company=company,
        name=name,
        token_prefix=raw_token[:8],
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        scopes=normalized_scopes,
        expires_at=expires_at,
        created_by=user,
    )
    return token, raw_token


def revoke_api_token(user: User, token) -> None:
    """Отзывает токен (is_active=False). Доступ: admin компании / superuser."""
    from external_api.models import ApiToken

    if not isinstance(token, ApiToken):
        raise ValidationError({"token": "Неверный токен"})
    _check_api_token_admin(user, token.company)
    if token.is_active:
        token.is_active = False
        token.save(update_fields=["is_active"])


def list_api_tokens(user: User, company: Company):
    """Список токенов компании. Доступ: admin / superuser."""
    from external_api.models import ApiToken

    _check_api_token_admin(user, company)
    return (
        ApiToken.objects.filter(company=company)
        .select_related("created_by")
        .order_by("-created_at")
    )

def get_project_specifications(project: Project):
    """Спецификации проекта."""
    return Specification.objects.filter(project=project).order_by(
        "section_code", "code"
    )


def create_vor_items(user: User, project: Project, items: list[dict]) -> list[VorItem]:
    """Создаёт строки ВОР с проверкой прав и валидацией входных данных."""
    if user is None or not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Нет прав на создание ВОР")
    role = _user_role(user)
    allowed = (
        user.is_superuser
        or _is_project_gip(user, project)
        or (
            role == "admin"
            and _check_company_access(user, project.company_id)
        )
    )
    if not allowed:
        raise PermissionDenied("Нет прав на создание ВОР")

    prepared = []
    for index, item in enumerate(items):
        code = (item.get("code") or "").strip()
        name = (item.get("name") or "").strip()
        unit = (item.get("unit") or "").strip()
        section_code = (item.get("section_code") or "").strip()
        if not code:
            raise ValidationError({f"items[{index}].code": "Код обязателен"})
        if not name:
            raise ValidationError({f"items[{index}].name": "Название обязательно"})
        if not unit:
            raise ValidationError({f"items[{index}].unit": "Ед. изм. обязательна"})
        try:
            quantity = Decimal(str(item.get("quantity")))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError({
                f"items[{index}].quantity": "Количество должно быть числом",
            })
        if quantity <= 0:
            raise ValidationError({
                f"items[{index}].quantity": "Количество должно быть > 0",
            })
        prepared.append({
            "section_code": section_code,
            "code": code,
            "name": name,
            "unit": unit,
            "quantity": quantity,
        })

    with transaction.atomic():
        return [
            VorItem.objects.create(project=project, **item)
            for item in prepared
        ]


def sync_issues(user: User, project: Project, issues_data: list[dict]) -> list[Issue]:
    """Создаёт или обновляет замечания проекта по данным внешней системы.

    При наличии external_id сопоставление идёт по нему в рамках проекта.
    Без external_id используется прежний fallback: document + description.
    Статус обновляется через сервисный слой с проверкой ролей.
    """
    synced = []
    for index, item in enumerate(issues_data):
        document = Document.objects.filter(
            project=project,
            code=item["document_code"],
            is_deleted=False,
        ).first()
        if document is None:
            raise ValidationError({
                f"issues[{index}].document_code": (
                    "Документ с таким кодом не найден в проекте"
                ),
            })
        if not check_issue_permission(user, document):
            raise PermissionDenied(
                "Нет прав на синхронизацию замечаний к документу"
            )

        text = (item.get("issue_text") or "").strip()
        if not text:
            raise ValidationError({
                f"issues[{index}].issue_text": "Текст замечания обязателен",
            })

        status = item.get("status") or "NEW"
        external_id = (item.get("external_id") or "").strip() or None
        if external_id:
            issue = (
                Issue.objects.filter(
                    document__project=project,
                    external_id=external_id,
                )
                .order_by("-created_at")
                .first()
            )
        else:
            issue = (
                Issue.objects.filter(
                    document=document,
                    description=text,
                )
                .order_by("-created_at")
                .first()
            )

        if issue is None:
            issue = create_issue(
                user,
                document,
                text,
                page_number=item.get("page_number"),
                x_ratio=item.get("x_ratio"),
                y_ratio=item.get("y_ratio"),
            )
            if external_id:
                issue.external_id = external_id
                issue.save(update_fields=["external_id"])
        else:
            update_fields = []
            if issue.description != text:
                issue.description = text
                update_fields.append("description")
            if external_id and issue.external_id != external_id:
                issue.external_id = external_id
                update_fields.append("external_id")
            if issue.document_id != document.pk:
                issue.document = document
                issue.version = None
                update_fields.extend(["document", "version"])
            if item.get("page_number") is not None:
                issue.page_number = item["page_number"]
                issue.x_ratio = item["x_ratio"]
                issue.y_ratio = item["y_ratio"]
                update_fields.extend(
                    ["page_number", "x_ratio", "y_ratio"]
                )
            if update_fields:
                issue.save(update_fields=update_fields)
        if status != issue.status:
            issue = update_issue_status(user, issue, status)
        synced.append(issue)
    return synced


def get_pending_issues(project: Project):
    """Замечания проекта в статусах NEW, IN_PROGRESS, REJECTED."""
    return (
        Issue.objects.filter(
            document__project=project,
            document__is_deleted=False,
            status__in=OPEN_ISSUE_STATUSES,
        )
        .select_related("document", "assignee")
        .order_by("-created_at")
    )


# =====================================================================
# Модуль «Задания» (DesignTask) — Этап 2, спека
# docs/DESIGN_TASKS_SPEC.md v1.0, раздел 3. Модели — Этап 1 (models.py),
# UI — Этапы 3–4. Бизнес-логика заданий живёт ТОЛЬКО здесь.
# =====================================================================

# URL карточки задания. Роут появится на Этапе 3 (спека 4.3:
# /projects/<id>/tasks/<task_id>/); до тех пор ссылка в уведомлениях
# ведёт на этот канонический адрес.
def _design_task_url(task: DesignTask) -> str:
    return f'/projects/{task.project_id}/tasks/{task.pk}/'


def _is_project_gip(user: User, project: Project) -> bool:
    """ГИП именно этого проекта: Project.gip или ProjectMembership(role=gip).

    UserProfile.role == 'gip' сам по себе права на чужой проект не даёт —
    в отличие от роли 'admin', которая глобальна.
    """
    if user.is_superuser:
        return True
    if project.gip_id == user.pk:
        return True
    return project.memberships.filter(user=user, role_in_project='gip').exists()


def _is_project_admin(user: User, project: Project) -> bool:
    """Админ: superuser или глобальная роль admin в UserProfile."""
    if user.is_superuser:
        return True
    profile = getattr(user, 'userprofile', None)
    return profile is not None and profile.role == 'admin'


def _is_gip_or_admin(user: User, project: Project) -> bool:
    return _is_project_gip(user, project) or _is_project_admin(user, project)


def _is_task_participant(user: User, task: DesignTask) -> bool:
    """Участник задания: автор, исполнитель, ГИП проекта или админ."""
    if task.author_id == user.pk or task.assignee_id == user.pk:
        return True
    return _is_gip_or_admin(user, task.project)


def _notify_task(
    task: DesignTask,
    recipient: User | None,
    actor: User,
    message: str,
    verb: str = 'status_changed',
) -> None:
    """Уведомление по заданию через существующий Notification.

    Verb'ы DesignTask в Notification.VERB_CHOICES отсутствуют (модель
    issues не расширялась — Этап 2 не трогает существующие модели),
    поэтому используются ближайшие по смыслу: 'assigned' при выдаче,
    'status_changed' при переходах. create_notification сам не шлёт
    уведомление актору и молча переживает ошибки.
    """
    create_notification(
        recipient=recipient,
        actor=actor,
        verb=verb,
        project=task.project,
        message=message,
        url=_design_task_url(task),
    )


def _log_status_change(
    task: DesignTask,
    old_status: str,
    new_status: str,
    user: User,
    comment: str = '',
) -> None:
    DesignTaskStatusLog.objects.create(
        task=task,
        old_status=old_status,
        new_status=new_status,
        changed_by=user,
        comment=comment or '',
    )


def create_task(
    user: User,
    project: Project,
    source_section: Section,
    target_section: Section,
    title: str,
    description: str,
    due_date=None,
    assignee: User | None = None,
    template: TaskTemplate | None = None,
    priority: str = 'normal',
) -> DesignTask:
    """Создание задания со статусом DRAFT (спека 3, create_task).

    Права: user должен иметь write-доступ к source_section
    (check_write_permission) — инициатором может быть любой представитель
    отдела-источника, не обязательно ГИП. При template копируется
    чек-лист; если срок не задан — берётся template.default_duration_days.
    """
    if source_section.project_id != project.pk or target_section.project_id != project.pk:
        raise ValueError("Разделы должны принадлежать проекту задания")
    if not check_write_permission(user, source_section):
        raise PermissionDenied("Нет прав на выдачу задания от этого раздела")
    if priority not in DesignTask.Priority.values:
        raise ValueError(f"Неизвестный приоритет: {priority}")
    if assignee is not None and assignee.pk == user.pk:
        # Себе задание «через себя» — допустимо? Спека не запрещает;
        # автор одновременно исполнитель — не блокируем.
        pass

    with transaction.atomic():
        if due_date is None and template is not None:
            due_date = timezone.localdate() + timedelta(
                days=template.default_duration_days
            )
        task = DesignTask.objects.create(
            project=project,
            source_section=source_section,
            target_section=target_section,
            author=user,
            assignee=assignee,
            template=template,
            title=title,
            description=description,
            due_date=due_date,
            status=DesignTask.Status.DRAFT,
            priority=priority,
        )
        if template is not None and template.checklist:
            DesignTaskChecklistItem.objects.bulk_create(
                DesignTaskChecklistItem(task=task, text=str(item))
                for item in template.checklist
            )
    logger.info(
        "create_task: user=%s task=%s project=%s %s→%s template=%s",
        user.pk, task.pk, project.code,
        source_section.code, target_section.code,
        template.pk if template else None,
    )
    return task


def issue_task(user: User, task: DesignTask) -> DesignTask:
    """Выдача задания: DRAFT → ISSUED (спека 3, issue_task).

    Права: только author или gip/admin. Заполняет issued_at, пишет лог и
    уведомляет исполнителя.
    """
    if task.status != DesignTask.Status.DRAFT:
        raise ValueError("Выдать можно только задание в статусе DRAFT")
    if task.author_id != user.pk and not _is_gip_or_admin(user, task.project):
        raise PermissionDenied("Выдать задание может автор, ГИП или админ")

    old_status = task.status
    with transaction.atomic():
        task.status = DesignTask.Status.ISSUED
        task.issued_at = timezone.now()
        task.save(update_fields=['status', 'issued_at', 'updated_at'])
        _log_status_change(task, old_status, DesignTask.Status.ISSUED, user)
        if task.assignee is not None:
            _notify_task(
                task,
                task.assignee,
                user,
                f'Вам выдано задание #{task.pk}: {task.title}',
                verb='assigned',
            )
    logger.info(
        "issue_task: user=%s task=%s assignee=%s",
        user.pk, task.pk, task.assignee_id,
    )
    return task


# Матрица переходов (спека 3): (old_status, new_status) → кто разрешён.
# Роли: A=assignee, AU=author, G=gip/admin. Условия (обязательный
# комментарий, output-attachment) проверяются отдельно.
_DESIGN_TASK_TRANSITIONS = {
    (DesignTask.Status.ISSUED, DesignTask.Status.IN_PROGRESS): {'A'},
    (DesignTask.Status.IN_PROGRESS, DesignTask.Status.ON_HOLD): {'A'},
    (DesignTask.Status.ON_HOLD, DesignTask.Status.IN_PROGRESS): {'A'},
    (DesignTask.Status.IN_PROGRESS, DesignTask.Status.COMPLETED): {'A'},
    (DesignTask.Status.COMPLETED, DesignTask.Status.REVIEW): {'AU', 'G'},
    (DesignTask.Status.REVIEW, DesignTask.Status.ACCEPTED): {'AU', 'G'},
    (DesignTask.Status.REVIEW, DesignTask.Status.RETURNED): {'AU', 'G'},
    (DesignTask.Status.RETURNED, DesignTask.Status.IN_PROGRESS): {'A'},
}

# Переходы «Любой → CANCELLED»: author/gip/admin, с комментарием.
# Терминальные статусы отменять нельзя: ACCEPTED уже закрыт заданием,
# CANCELLED — повторная отмена бессмысленна (интерпретация «Любой»,
# см. отчёт аудита).
_DESIGN_TASK_FINAL_STATUSES = {
    DesignTask.Status.ACCEPTED,
    DesignTask.Status.CANCELLED,
}

# Переходы, требующие непустого комментария (колонка «Условие» матрицы).
_COMMENT_REQUIRED = {
    DesignTask.Status.ON_HOLD,
    DesignTask.Status.RETURNED,
    DesignTask.Status.CANCELLED,
}


def update_task_status(
    user: User,
    task: DesignTask,
    new_status: str,
    comment: str = '',
) -> DesignTask:
    """Смена статуса по матрице переходов (спека 3, update_task_status).

    Права — по матрице (assignee / author / gip / admin). Недопустимый
    переход или нарушенное условие → ValidationError; отсутствие прав →
    PermissionDenied. Каждый переход пишется в DesignTaskStatusLog;
    ключевые переходы уведомляют участников (COMPLETED → author/gip,
    RETURNED → assignee, ACCEPTED → assignee).
    """
    if new_status not in DesignTask.Status.values:
        raise ValueError(f"Неизвестный статус: {new_status}")
    if new_status == task.status:
        raise ValueError("Новый статус совпадает с текущим")

    old_status = task.status

    if new_status == DesignTask.Status.CANCELLED:
        if old_status in _DESIGN_TASK_FINAL_STATUSES:
            raise ValidationError(
                f"Нельзя отменить задание в статусе {old_status}"
            )
        if task.author_id != user.pk and not _is_gip_or_admin(user, task.project):
            raise PermissionDenied("Отменить задание может автор, ГИП или админ")
    else:
        allowed = _DESIGN_TASK_TRANSITIONS.get((old_status, new_status))
        if allowed is None:
            raise ValidationError(
                f"Переход {old_status} → {new_status} запрещён матрицей"
            )
        if not (
            ('A' in allowed and task.assignee_id == user.pk)
            or ('AU' in allowed and task.author_id == user.pk)
            or ('G' in allowed and _is_gip_or_admin(user, task.project))
        ):
            raise PermissionDenied(
                f"Переход {old_status} → {new_status} недоступен вашей роли"
            )

    comment = (comment or '').strip()
    if new_status in _COMMENT_REQUIRED and not comment:
        raise ValidationError(
            f"Переход в статус {new_status} требует комментария"
        )

    if (
        new_status == DesignTask.Status.COMPLETED
        and not task.attachments.filter(kind=DesignTaskAttachment.Kind.OUTPUT).exists()
    ):
        raise ValidationError(
            "Перед отметкой выполнения приложите результат (output)"
        )

    with transaction.atomic():
        task.status = new_status
        update_fields = ['status', 'updated_at']
        if new_status == DesignTask.Status.ACCEPTED:
            task.accepted_at = timezone.now()
            task.closed_by = user
            update_fields += ['accepted_at', 'closed_by']
        task.save(update_fields=update_fields)
        _log_status_change(task, old_status, new_status, user, comment)

        if new_status == DesignTask.Status.COMPLETED:
            for recipient in {task.author, task.project.gip} - {None, user}:
                _notify_task(
                    task,
                    recipient,
                    user,
                    f'Задание #{task.pk} выполнено и ждёт проверки: {task.title}',
                )
        elif new_status == DesignTask.Status.RETURNED:
            _notify_task(
                task,
                task.assignee,
                user,
                f'Задание #{task.pk} возвращено на доработку: {comment[:200]}',
            )
        elif new_status == DesignTask.Status.ACCEPTED:
            _notify_task(
                task,
                task.assignee,
                user,
                f'Задание #{task.pk} принято: {task.title}',
            )

    logger.info(
        "update_task_status: user=%s task=%s %s→%s",
        user.pk, task.pk, old_status, new_status,
    )
    return task


def add_task_attachment(
    user: User,
    task: DesignTask,
    kind: str,
    document: Document | None = None,
    file=None,
) -> DesignTaskAttachment:
    """Вложение к заданию (спека 3, add_task_attachment).

    Права по kind: input — author или gip/admin (передача исходных
    данных), output — только assignee (результат, только в статусе
    IN_PROGRESS), comment — любые участники. Требуется хотя бы одно из
    document/file.
    """
    if kind not in DesignTaskAttachment.Kind.values:
        raise ValueError(f"Неизвестный тип вложения: {kind}")
    if document is None and file is None:
        raise ValidationError("Вложение требует документ или файл")

    is_gip_or_admin = _is_gip_or_admin(user, task.project)
    if kind == DesignTaskAttachment.Kind.INPUT:
        if task.author_id != user.pk and not is_gip_or_admin:
            raise PermissionDenied(
                "Входные данные добавляет автор задания, ГИП или админ"
            )
    elif kind == DesignTaskAttachment.Kind.OUTPUT:
        if task.assignee_id != user.pk:
            raise PermissionDenied("Результат добавляет только исполнитель")
        if task.status != DesignTask.Status.IN_PROGRESS:
            raise ValidationError(
                "Результат можно прикладывать только в статусе В работе"
            )
    else:  # comment
        if not _is_task_participant(user, task):
            raise PermissionDenied(
                "Комментарии оставляют только участники задания"
            )

    attachment = DesignTaskAttachment.objects.create(
        task=task,
        document=document,
        file=file,
        kind=kind,
        uploaded_by=user,
    )
    logger.info(
        "add_task_attachment: user=%s task=%s kind=%s attachment=%s",
        user.pk, task.pk, kind, attachment.pk,
    )
    return attachment


def toggle_checklist_item(
    user: User,
    item: DesignTaskChecklistItem,
) -> DesignTaskChecklistItem:
    """Отметка/снятие пункта чек-листа (спека 3, toggle_checklist_item).

    Права: только assignee или gip/admin. При выполнении фиксируются
    done_by/done_at; при снятии — сбрасываются.
    """
    task = item.task
    if task.assignee_id != user.pk and not _is_gip_or_admin(user, task.project):
        raise PermissionDenied("Чек-лист отмечает исполнитель, ГИП или админ")

    item.is_done = not item.is_done
    if item.is_done:
        item.done_by = user
        item.done_at = timezone.now()
    else:
        item.done_by = None
        item.done_at = None
    item.save(update_fields=['is_done', 'done_by', 'done_at'])
    logger.info(
        "toggle_checklist_item: user=%s item=%s is_done=%s",
        user.pk, item.pk, item.is_done,
    )
    return item


def task_summary_for_gip(project: Project) -> dict:
    """Сводка по заданиям проекта для дашборда ГИПа (спека 3, 4.4).

    overdue: due_date < сегодня и статус не в терминальных
    (ACCEPTED, CANCELLED). awaiting_review: статус REVIEW.
    """
    today = timezone.localdate()
    queryset = DesignTask.objects.filter(project=project)
    by_status_counts = queryset.values('status').annotate(total=Count('id'))
    by_status = {row['status']: row['total'] for row in by_status_counts}
    return {
        'total': queryset.count(),
        'by_status': by_status,
        'overdue': queryset.filter(
            due_date__lt=today,
            status__in=[
                DesignTask.Status.DRAFT,
                DesignTask.Status.ISSUED,
                DesignTask.Status.IN_PROGRESS,
                DesignTask.Status.ON_HOLD,
                DesignTask.Status.COMPLETED,
                DesignTask.Status.REVIEW,
                DesignTask.Status.RETURNED,
            ],
        ).count(),
        'on_hold': by_status.get(DesignTask.Status.ON_HOLD, 0),
        'awaiting_review': by_status.get(DesignTask.Status.REVIEW, 0),
    }


def can_view_all_tasks(user: User, project: Project) -> bool:
    """Видит ли пользователь все задания проекта (спека 3, list_tasks 'all').

    Тонкая обёртка над _is_gip_or_admin — тот же предикат, что и
    PermissionDenied-гейт внутри list_tasks('all'). Нужна вкладке
    «Задания» (спека 4.1, Этап 3): сегмент-контрол «Все / Входящие /
    Исходящие» показывает сегмент «Все» только тем, кого сервис не
    отклонит, — UI и права не расходятся. Новая функция, существующие
    сервисы Этапа 2 не изменены.
    """
    return _is_gip_or_admin(user, project)


def list_tasks(
    project: Project,
    user: User,
    role_filter: str = 'all',
) -> QuerySet:
    """Список заданий проекта (спека 3, list_tasks).

    role_filter: 'incoming' — назначенные мне; 'outgoing' — созданные
    мной; 'all' — все задания (только gip/admin проекта, иначе
    PermissionDenied). Доступ к проекту обязателен для всех вариантов.
    """
    _ensure_project_access(user, project)
    if role_filter not in ('incoming', 'outgoing', 'all'):
        raise ValueError(f"Неизвестный role_filter: {role_filter}")
    if role_filter == 'all' and not _is_gip_or_admin(user, project):
        raise PermissionDenied(
            "Все задания проекта видит только ГИП или админ"
        )
    queryset = (
        DesignTask.objects.filter(project=project)
        .select_related(
            'project', 'source_section', 'target_section',
            'author', 'assignee', 'template',
        )
    )
    if role_filter == 'incoming':
        queryset = queryset.filter(assignee=user)
    elif role_filter == 'outgoing':
        queryset = queryset.filter(author=user)
    return queryset.order_by('-created_at')
