from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Prefetch, Q
from django.db import transaction
from django.utils import timezone

from storage.domain.models import (
    Company,
    Document,
    DocumentView,
    DocumentVersion,
    Project,
    ProjectMembership,
    Section,
    User,
)


DOCUMENT_VIEW_COLUMNS = ('code', 'name', 'doc_type', 'status', 'version', 'date', 'author')
DOCUMENT_VIEW_FILTERS = ('section', 'doc_type', 'status')
DOCUMENT_VIEW_SORT_FIELDS = ('code', 'name', 'date', 'version')


def _document_row(document: Document) -> dict:
    """Единый формат строки документа для ведомости и видов таблицы."""
    version = document.current_version
    author = ''
    if version is not None:
        author = version.uploaded_by.get_full_name() or version.uploaded_by.username

    return {
        'id': document.pk,
        'code': document.code,
        'name': document.name,
        'doc_type': document.get_doc_type_display(),
        'doc_type_code': document.doc_type,
        'status': document.get_status_display(),
        'status_code': document.status,
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
    queryset = Document.objects.all()
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

    unknown_keys = set(config) - {'columns', 'filters', 'sort'}
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

    return {'columns': columns, 'filters': filters, 'sort': normalized_sort}


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
        document_count=Count('documents', distinct=True),
    )


def get_project_documents_for_view(
    user: User,
    project: Project,
    config: dict | None = None,
) -> list[dict]:
    """Документы проекта для таблицы с учётом сохранённого вида."""
    _ensure_project_access(user, project)

    normalized_config = normalize_document_view_config(config or {})
    queryset = get_accessible_documents(user).filter(project=project)

    filters = normalized_config['filters']
    if 'section' in filters:
        queryset = queryset.filter(section_id=filters['section'])
    if 'doc_type' in filters:
        queryset = queryset.filter(doc_type=filters['doc_type'])
    if 'status' in filters:
        queryset = queryset.filter(status=filters['status'])

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

    documents = queryset.select_related(
        'section',
        'current_version',
        'current_version__uploaded_by',
    ).order_by(*ordering)

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

    if membership.role_in_project == 'gip' and role != 'gip':
        other_gips = membership.project.memberships.filter(
            role_in_project='gip',
        ).exclude(pk=membership.pk)
        if not other_gips.exists() and membership.project.gip_id is None:
            raise ValueError("Нельзя снять роль последнего ГИП проекта")

    membership.role_in_project = role
    membership.save(update_fields=['role_in_project'])
    return membership


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

    if not _check_company_access(actor, company.pk):
        raise PermissionDenied("Нет прав на создание проекта в этой компании")

    if gip is not None and not _check_company_access(gip, company.pk):
        raise PermissionDenied("ГИП должен принадлежать компании проекта")

    return Project.objects.create(
        company=company,
        code=code,
        name=name,
        customer=customer,
        gip=gip,
        status='active',
        created_by=actor,
    )


def check_write_permission(user: User, section: Section) -> bool:
    """Проверка права на запись в раздел.

    Правила (всего 3 должности):
    - admin — всегда может писать в своей компании.
    - gip — может писать только в свои проекты (через Project.gip или ProjectMembership).
    - engineer — может писать только в разрешенные разделы (allowed_sections).
    - viewer и contractor — не могут писать.

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

    if role == 'engineer':
        return section in profile.allowed_sections.all()

    # viewer, contractor и прочие — не могут писать
    return False


def check_read_permission(user: User, document: Document) -> bool:
    """Проверка права на чтение документа.

    - admin и gip (в своих проектах) читают всё.
    - engineer читает всё в своих проектах.
    - viewer читает всё в своей компании.
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

    if role in ('admin', 'gip'):
        if role == 'gip' and project.gip_id != user.id:
            # gip читает только свои проекты
            return False
        return True

    if role == 'engineer':
        # engineer читает всё в своих проектах
        # (тут упрощённо: engineer может читать документ, если его проект
        # пересекается с проектами, где у него есть membership gip/engineer,
        # но по §3.2 engineer читает «всего в своих проектах».
        # Для простоты и соответствия §3.2 считаем, что engineer читает
        #документ, если он принадлежит проекту, где у пользователя membership
        #с ролью gip или engineer.
        # В текущей реализации проверяем через memberships:
        from storage.domain.models import ProjectMembership
        if project.memberships.filter(
            user=user, role_in_project__in=('gip', 'engineer')
        ).exists():
            return True
        return False

    if role == 'viewer':
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
) -> DocumentVersion:
    """Единственная точка создания версий документа.

    - Проверка прав через check_write_permission.
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
                'created_by': user,
            },
        )
        if not created:
            # Обновляем поля, если документ уже существует
            doc.name = name
            doc.doc_type = doc_type
            doc.save(update_fields=['name', 'doc_type', 'updated_at'])

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
        qs = Document.objects.all()

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
