from django.core.exceptions import PermissionDenied
from django.db.models import Max
from django.db import transaction
from django.utils import timezone

from storage.domain.models import Document, DocumentVersion, Section, Project, User


def check_write_permission(user: User, section: Section) -> bool:
    """Проверка права на запись в раздел.

    Правила (всего 3 должности):
    - admin — всегда может писать.
    - gip — может писать только в свои проекты (через Project.gip или ProjectMembership).
    - engineer — может писать только в разрешенные разделы (allowed_sections).
    - viewer и contractor — не могут писать.
    """
    try:
        profile = user.userprofile
    except Exception:
        # Профиля нет (например, суперюзер без создания профиля) — отказ
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
    - viewer читает всё.
    - contractor читает только если раздел документа в allowed_sections.
    """
    try:
        profile = user.userprofile
    except Exception:
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


def generate_vedomost(project: Project, section=None) -> list:
    """Формирует список словарей для ведомости документов.

    Использует select_related для оптимизации запросов к профилям
    и версии, order_by для сортировки по разделу и шифру.
    """
    qs = Document.objects.filter(project=project).select_related(
        'current_version',
        'current_version__uploaded_by',
        'created_by',
    ).order_by('section__order', 'code')

    if section is not None:
        qs = qs.filter(section=section)

    result = []
    for doc in qs:
        result.append({
            'id': doc.pk,
            'code': doc.code,
            'name': doc.name,
            'doc_type': doc.get_doc_type_display(),
            'version': doc.current_version.version_number if doc.current_version else '-',
            'version_id': doc.current_version_id,
            'date': doc.current_version.uploaded_at if doc.current_version else '',
            'author': doc.current_version.uploaded_by.get_full_name() or doc.current_version.uploaded_by.username if doc.current_version else '',
        })
    return result


def get_document_versions(user: User, document_id: int):
    document = Document.objects.get(pk=document_id)
    if not check_read_permission(user, document):
        raise PermissionDenied("Нет прав на чтение документа")
    return document.versions.all().order_by('-version_number')