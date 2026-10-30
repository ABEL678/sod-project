import base64
from collections import defaultdict
import io
import uuid

import fitz

from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db.models import Count, Max, Prefetch, Q, QuerySet
from django.db import transaction
from django.utils import timezone

from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    DocumentView,
    Folder,
    MailRule,
    Project,
    ProjectMembership,
    Section,
    User,
)
from issues.models import Issue


DOCUMENT_VIEW_COLUMNS = ('code', 'name', 'doc_type', 'status', 'zone', 'version', 'date', 'author')
DOCUMENT_VIEW_FILTERS = ('section', 'doc_type', 'status', 'code', 'name')
DOCUMENT_VIEW_SORT_FIELDS = ('code', 'name', 'date', 'version')
DOCUMENT_VIEW_MIN_WIDTH = 50
DOCUMENT_VIEW_MAX_WIDTH = 800

CANONICAL_ZONE_NAMES = {
    '01. В работе',
    '02. На согласовании',
    '03. Опубликовано',
    '04. Архив',
}

ZONE_STATUS_MAP = {
    'WIP': 'draft',
    'SHARED': 'in_review',
    'PUBLISHED': 'approved',
    'ARCHIVE': 'archived',
}


def _status_for_zone(folder: Folder | None) -> str | None:
    if not _is_canonical_zone_folder(folder):
        return None
    return ZONE_STATUS_MAP.get(folder.zone)


def _is_canonical_zone_folder(folder: Folder | None) -> bool:
    """Проверяет, является ли папка канонической зоной проекта.

    Каноническая зона — это корневая папка (parent=None, section=None)
    с одним из 4 канонических имён.
    """
    if folder is None:
        return False
    return (
        folder.parent is None
        and folder.section is None
        and folder.name in CANONICAL_ZONE_NAMES
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


def _check_company_access(user: User, company_id: int) -> bool:
    """Проверяет, что пользователь принадлежит компании (superuser — вне ограничений)."""
    if user.is_superuser:
        return True
    company = get_user_company(user)
    return company is not None and company.pk == company_id


def get_accessible_projects(user: User):
    """Проекты компании пользователя (superuser видит все)."""
    queryset = Project.objects.all()
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
    return get_accessible_sections(user, project).annotate(
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
) -> QuerySet:
    """Возвращает queryset документов проекта с учётом сохранённого вида.

    ``folder`` ограничивает выборку документами конкретной папки: навигация
    по дереву папок не должна подмешивать документы соседних папок.
    """
    _ensure_project_access(user, project)

    normalized_config = normalize_document_view_config(config or {})
    queryset = get_accessible_documents(user).filter(project=project)

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

    return queryset.select_related(
        'section',
        'current_version',
        'current_version__uploaded_by',
        'created_by',
    ).order_by(*ordering)


def get_project_documents_for_view(
    user: User,
    project: Project,
    config: dict | None = None,
    folder: Folder | None = None,
) -> list[dict]:
    """Документы проекта для таблицы с учётом сохранённого вида и папки."""
    documents = get_project_documents_queryset(user, project, config, folder=folder)
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


def _create_default_zones(project: Project, created_by: User) -> None:
    """Создаёт 4 корневые папки-зоны для проекта."""
    zone_configs = [
        ('WIP', '01. В работе'),
        ('SHARED', '02. На согласовании'),
        ('PUBLISHED', '03. Опубликовано'),
        ('ARCHIVE', '04. Архив'),
    ]
    for idx, (zone, name) in enumerate(zone_configs, 1):
        Folder.objects.create(
            project=project,
            section=None,
            parent=None,
            name=name,
            order=idx,
            zone=zone,
            created_by=created_by,
        )


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
        Folder.objects.create(
            project=project,
            section=None,
            parent=None,
            name=zone_name_map[zone],
            order=zone_order_map[zone],
            zone=zone,
            created_by=actor,
        )
    return True


def create_project(
    company: Company,
    code: str,
    name: str,
    customer: str,
    gip: User | None = None,
    created_by: User | None = None,
) -> Project:
    """Создает проект в указанной компании.

    Создатель по умолчанию — ГИП. Создатель и ГИП должны принадлежать компании,
    если они не superuser.
    """
    actor = created_by or gip
    if actor is None:
        raise ValueError("Не указан создатель проекта")

    _validate_gip_role(gip)

    code = (code or '').strip()
    name = (name or '').strip()
    if not code or not name:
        raise ValueError("Шифр и название проекта обязательны")
    if Project.objects.filter(code=code).exists():
        raise ValueError("Проект с таким шифром уже существует")

    if not _check_company_access(actor, company.pk):
        raise PermissionDenied("Нет прав на создание проекта в этой компании")

    if gip is not None and not _check_company_access(gip, company.pk):
        raise PermissionDenied("ГИП должен принадлежать компании проекта")

    project = Project.objects.create(
        company=company,
        code=code,
        name=name,
        customer=customer,
        gip=gip,
        status='active',
        created_by=actor,
    )

    _create_default_zones(project, actor)

    return project


def can_create_project(user: User) -> bool:
    """Может ли пользователь создавать проекты через интерфейс."""
    if user.is_superuser:
        return True
    profile = getattr(user, 'userprofile', None)
    return profile is not None and profile.role in ('admin', 'gip')


def create_section(project: Project, code: str, name: str, order: int, user: User | None = None) -> Section:
    """Создаёт раздел проекта."""
    if user is not None:
        _ensure_content_manager(user, project)
    code = (code or '').strip()
    name = (name or '').strip()
    if not code or not name:
        raise ValueError("Код и название раздела обязательны")
    if Section.objects.filter(project=project, code=code).exists():
        raise ValueError("Раздел с таким кодом уже существует")
    return Section.objects.create(
        project=project,
        code=code,
        name=name,
        order=int(order or 0),
    )


def _ensure_content_manager(user: User, project: Project) -> None:
    _ensure_project_access(user, project)
    if not can_manage_project_participants(user, project):
        raise PermissionDenied("Нет прав на управление структурой проекта")


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


def create_folder(
    user: User,
    project: Project,
    name: str,
    section: Section | None = None,
    parent: Folder | None = None,
) -> Folder:
    """Создаёт папку проекта или вложенную папку."""
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
        if parent.section_id is not None:
            if section is not None and section.pk != parent.section_id:
                raise ValueError("Раздел папки не совпадает с разделом родителя")
            section = parent.section

    max_order = Folder.objects.filter(project=project, parent=parent).aggregate(
        Max('order')
    )['order__max']
    return Folder.objects.create(
        project=project,
        section=section,
        parent=parent,
        name=name,
        order=(max_order or 0) + 1,
        created_by=user,
    )


def rename_folder(user: User, folder: Folder, name: str) -> Folder:
    """Переименовывает папку."""
    _ensure_content_manager(user, folder.project)
    name = (name or '').strip()
    if not name:
        raise ValueError("Название папки обязательно")
    folder.name = name
    folder.save(update_fields=['name'])
    return folder


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
        .select_related('section')
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


def move_document(user: User, document: Document, folder: Folder | None) -> Document:
    """Перемещает документ в папку проекта."""
    if not check_write_permission(user, document.section):
        raise PermissionDenied("Нет прав на перемещение документа")
    if document.is_deleted:
        raise ValueError("Нельзя перемещать документ из корзины")

    # Проверка прав на покидание текущей зоны
    current_zone = getattr(document.folder, 'zone', None) if document.folder else None
    if current_zone in ('ARCHIVE', 'PUBLISHED', 'SHARED'):
        profile = getattr(user, 'userprofile', None)
        if profile is None or profile.role != 'admin':
            zone_name = document.folder.get_zone_display() if document.folder else 'зона'
            raise PermissionDenied(
                f"Документ в зоне «{zone_name}» — перемещение только для администратора"
            )

    if folder is not None:
        if folder.project_id != document.project_id:
            raise ValueError("Папка относится к другому проекту")
        if folder.is_deleted:
            raise ValueError("Нельзя переместить документ в удалённую папку")
        if folder.section_id is not None and folder.section_id != document.section_id:
            raise ValueError("Папка относится к другому разделу")

        # Проверка прав на запись в целевую зону
        if not check_zone_permission(user, folder, document.section):
            zone_name = folder.get_zone_display()
            raise PermissionDenied(f"Нет прав на запись в зону «{zone_name}»")

    document.folder = folder
    update_fields = ['folder', 'updated_at']

    # Автоматическое изменение статуса только для канонических зон
    target_status = _status_for_zone(folder)
    if target_status is not None and target_status != document.status:
        document.status = target_status
        update_fields.append('status')

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
            status=document.status,
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


def move_folder(
    user: User,
    folder: Folder,
    parent: Folder | None = None,
    section: Section | None = None,
) -> Folder:
    """Перемещает папку в другую папку или в корень раздела."""
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
        if (
            folder.section_id is not None
            and parent.section_id is not None
            and folder.section_id != parent.section_id
        ):
            raise ValueError("Нельзя перемещать папку между разделами")
        new_section = parent.section if parent.section_id else folder.section
    elif section is not None:
        if section.project_id != folder.project_id:
            raise ValueError("Раздел относится к другому проекту")
        if folder.section_id is not None and folder.section_id != section.pk:
            raise ValueError("Нельзя перемещать папку между разделами")
        new_section = section
    else:
        new_section = folder.section

    folder.parent = parent
    folder.section = new_section
    folder.save(update_fields=['parent', 'section'])
    return folder


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
    """Генерирует и сохраняет публичный токен документа."""
    if not check_read_permission(user, document):
        raise PermissionDenied("Нет прав на создание ссылки")
    if document.is_deleted:
        raise ValueError("Нельзя создать ссылку на удалённый документ")

    if not document.public_link_token:
        document.public_link_token = uuid.uuid4()
        document.save(update_fields=['public_link_token'])
    return str(document.public_link_token)


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
    for version in versions:
        if version.file:
            file_storage.delete(version.file.name)

    with transaction.atomic():
        Document.objects.filter(project=project, is_deleted=True).delete()
        Folder.objects.filter(project=project, is_deleted=True).delete()

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
) -> bool:
    """Проверка прав записи с учётом зоны папки.

    Правила зон:
    - WIP: разработчики, смежники, ГИП, админ (через check_write_permission).
    - SHARED: только ГИП и админ.
    - PUBLISHED: только админ.
    - ARCHIVE: никто не может писать (только чтение).
    - folder=None: используются стандартные правила check_write_permission.
    """
    if folder is None:
        # Нет папки — стандартная проверка по секции
        if section is not None:
            return check_write_permission(user, section)
        return True

    zone = folder.zone
    if user.is_superuser:
        return True

    profile = getattr(user, 'userprofile', None)
    if profile is None:
        return False

    # ARCHIVE — админ может перемещать документы (архивировать)
    # Но загрузка новых версий в документ из ARCHIVE блокируется отдельно в upload_document
    if zone == 'ARCHIVE':
        return profile.role == 'admin'

    # PUBLISHED — только админ компании
    if zone == 'PUBLISHED':
        return profile.role == 'admin'

    # SHARED — только ГИП проекта и админ
    if zone == 'SHARED':
        if profile.role == 'admin':
            return True
        if profile.role == 'gip':
            project = folder.project
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
        project = folder.project
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


def upload_document(
    user: User,
    project: Project,
    section: Section,
    code: str,
    name: str,
    doc_type: str,
    file,
    comment: str = "",
    folder: Folder | None = None,
) -> DocumentVersion:
    """Единственная точка создания версий документа.

    - Проверка прав через check_write_permission.
    - Проверка прав по зоне через check_zone_permission.
    - Проверка соответствия section.project == project.
    - Поиск/создание документа.
    - Автоинкремент номера версии.
    - Создание DocumentVersion с file_size и uploaded_by.
    - Обновление current_version и updated_at у документа.
    """
    if not check_write_permission(user, section):
        raise PermissionDenied("Нет прав на запись в этот раздел")

    if section.project != project:
        raise ValueError("Раздел не относится к указанному проекту")

    # Проверяем текущую зону документа (если документ уже существует и имеет папку)
    existing_doc = Document.objects.filter(
        project=project,
        section=section,
        code=code,
    ).select_related('folder').first()
    if existing_doc is not None and existing_doc.folder is not None:
        if existing_doc.folder.zone == 'ARCHIVE':
            raise PermissionDenied(
                "Документ в архиве — загрузка новых версий запрещена"
            )
        if not check_zone_permission(user, existing_doc.folder, section):
            zone_name = existing_doc.folder.get_zone_display()
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
                'status': 'draft',
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
                if target_status is not None and target_status != doc.status:
                    doc.status = target_status
                    update_fields.append('status')
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


def get_document_versions(user: User, document_id: int):
    document = Document.objects.get(pk=document_id)
    if not check_read_permission(user, document):
        raise PermissionDenied("Нет прав на чтение документа")
    return document.versions.all().order_by('-version_number')


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

    return Issue.objects.create(
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


def update_issue_answer(user: User, issue: Issue, answer_text: str) -> Issue:
    """Сохраняет текст ответа на замечание."""
    if not check_read_permission(user, issue.document):
        raise PermissionDenied("Нет прав на изменение замечания")
    issue.answer_text = answer_text
    issue.save(update_fields=['answer_text', 'updated_at'])
    return issue


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


PDF_ISSUE_COLORS = {
    'NEW': (0.05, 0.43, 0.99),
    'IN_PROGRESS': (0.05, 0.79, 0.94),
    'DISCUSSION': (1.0, 0.76, 0.03),
    'REJECTED': (0.86, 0.21, 0.27),
    'DONE': (0.10, 0.53, 0.33),
    'CLOSED': (0.42, 0.46, 0.49),
    'CANCELLED': (0.13, 0.15, 0.16),
}


def export_pdf_with_issues(user: User, document: Document) -> bytes:
    """Возвращает PDF текущей версии с наложенными маркапами замечаний."""
    if not check_read_permission(user, document):
        raise PermissionDenied('Нет прав на скачивание документа')

    version = document.current_version
    if version is None or not version.file:
        raise ValueError('У документа нет текущей версии')
    if not version.file.storage.exists(version.file.name):
        raise ValueError('Файл текущей версии не найден')

    issues = document.issues.filter(page_number__isnull=False).order_by('pk')
    pdf = fitz.open(version.file.path)
    try:
        for issue in issues:
            page_index = issue.page_number - 1
            if page_index < 0 or page_index >= pdf.page_count:
                continue
            page = pdf[page_index]
            page_rect = page.rect
            color = PDF_ISSUE_COLORS.get(issue.status, PDF_ISSUE_COLORS['NEW'])

            if (
                issue.x1_ratio is not None and issue.y1_ratio is not None
                and issue.x2_ratio is not None and issue.y2_ratio is not None
            ):
                x1 = page_rect.x0 + page_rect.width * min(issue.x1_ratio, issue.x2_ratio)
                y1 = page_rect.y0 + page_rect.height * min(issue.y1_ratio, issue.y2_ratio)
                x2 = page_rect.x0 + page_rect.width * max(issue.x1_ratio, issue.x2_ratio)
                y2 = page_rect.y0 + page_rect.height * max(issue.y1_ratio, issue.y2_ratio)
                rect = fitz.Rect(x1, y1, x2, y2)
                page.draw_rect(rect, color=color, width=2)
                page.insert_text(
                    fitz.Point(rect.x0 + 3, max(rect.y0 - 3, 8)),
                    f'#{issue.pk}',
                    fontsize=8,
                    color=color,
                )
            elif issue.x_ratio is not None and issue.y_ratio is not None:
                x = page_rect.x0 + page_rect.width * issue.x_ratio
                y = page_rect.y0 + page_rect.height * issue.y_ratio
                page.draw_circle(fitz.Point(x, y), 9, color=color, width=2)
                page.insert_text(
                    fitz.Point(x + 12, max(y - 12, 8)),
                    f'#{issue.pk}',
                    fontsize=8,
                    color=color,
                )

        buffer = io.BytesIO()
        pdf.save(buffer)
        return buffer.getvalue()
    finally:
        pdf.close()
