from copy import copy

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Company(models.Model):
    name = models.CharField(max_length=255, verbose_name="Название")
    logo = models.ImageField(
        upload_to='company_logos/',
        null=True,
        blank=True,
        verbose_name='Логотип',
    )
    is_private = models.BooleanField(
        default=False,
        verbose_name='Приватная компания',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Компания"
        verbose_name_plural = "Компании"

    def __str__(self):
        return self.name


class Project(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='projects', verbose_name="Компания")
    code = models.CharField(max_length=50, unique=True, verbose_name="Шифр")
    base_code = models.CharField(
        max_length=50,
        blank=True,
        default='',
        verbose_name="Базовое обозначение",
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    customer = models.CharField(max_length=255, blank=True, verbose_name="Заказчик")
    gip = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='gip_projects', verbose_name="ГИП")
    STATUS_CHOICES = [
        ('active', 'Активен'),
        ('archived', 'Архив'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, verbose_name="Статус")
    template_key = models.CharField(
        max_length=20,
        blank=True,
        default='',
        verbose_name="Ключ шаблона структуры",
    )
    is_deleted = models.BooleanField(default=False, verbose_name="Удален")
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата удаления")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_projects', verbose_name="Создатель")

    class Meta:
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"

    def __str__(self):
        return f'{self.code} - {self.name}'


class Section(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='sections', verbose_name="Проект")
    code = models.CharField(max_length=20, verbose_name="Код раздела")
    full_code = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name="Полный шифр",
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    order = models.IntegerField(verbose_name="Порядок сортировки")
    folder_template = models.CharField(max_length=100, blank=True, verbose_name="Шаблон папки")

    class Meta:
        unique_together = ('project', 'code')
        verbose_name = "Раздел"
        verbose_name_plural = "Разделы"

    def __str__(self):
        return f'{self.code} - {self.name}'


class Document(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='documents', verbose_name="Проект")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='documents', verbose_name="Раздел")
    folder = models.ForeignKey(
        'Folder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
        verbose_name="Папка",
    )
    code = models.CharField(max_length=100, verbose_name="Шифр документа")
    name = models.CharField(max_length=255, verbose_name="Название")
    DOC_TYPE_CHOICES = [
        ('drawing', 'Чертёж'),
        ('source_data', 'Исходные данные'),
        ('report', 'Отчет'),
        ('other', 'Другое'),
    ]
    doc_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES, verbose_name="Тип документа")
    current_version = models.ForeignKey(
        'DocumentVersion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name="Текущая версия"
    )
    STATUS_CHOICES = [
        ('draft', 'Черновик'),
        ('in_review', 'На проверке'),
        ('approved', 'Утвержден'),
        ('archived', 'Архив'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, verbose_name="Статус")
    # Требование владельца (UI-аудит): в интерфейсе коды S0–S4, A, B, CR
    # не выводятся — только официальные русские названия ниже. Значения
    # для БД не меняются, поэтому миграция не требуется.
    SUITABILITY_CHOICES = [
        ('S0', 'Черновик'),
        ('S1', 'Для совместной работы'),
        ('S2', 'Для ознакомления'),
        ('S3', 'На проверке'),
        ('S4', 'На утверждении'),
        ('A', 'Утверждён'),
        ('B', 'Утверждено с замечаниями'),
        ('CR', 'Утверждён'),
    ]
    # Пояснения к статусам документа для карточки (тултип/подпись).
    SUITABILITY_DESCRIPTIONS = {
        'S0': 'Документ в разработке, не передаётся другим подразделениям.',
        'S1': 'Документ передан смежным отделам для учёта в их работе.',
        'S2': 'Документ передан для сведения, изменения по нему не вносятся.',
        'S3': 'Документ проверяют ГИП, проверяющий или нормоконтролёр.',
        'S4': 'Документ передан Заказчику для утверждения.',
        'A': 'Документ утверждён Заказчиком, допущен к использованию.',
        'B': 'Документ утверждён Заказчиком с замечаниями.',
        'CR': 'Исполнительная документация утверждена Заказчиком.',
    }
    suitability_code = models.CharField(
        max_length=2,
        choices=SUITABILITY_CHOICES,
        default='S0',
        verbose_name="Код пригодности",
    )
    INTERNAL_STATUS_CHOICES = [
        ('draft', 'В работе'),
        ('internal_review', 'На внутренней проверке'),
        ('rework', 'На доработке'),
        ('ready_to_share', 'Готов к передаче'),
    ]
    internal_status = models.CharField(
        max_length=20,
        choices=INTERNAL_STATUS_CHOICES,
        default='draft',
        verbose_name="Внутренний статус",
    )
    public_link_token = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        verbose_name="Публичный токен",
    )
    public_link_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Публичная ссылка действует до",
    )
    is_deleted = models.BooleanField(default=False, verbose_name="Удален")
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата удаления")
    deleted_with_parent = models.BooleanField(
        default=False,
        verbose_name="Удален вместе с родителем",
    )
    deleted_with_project = models.BooleanField(
        default=False,
        verbose_name="Удален вместе с проектом",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="Создатель")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        unique_together = ('project', 'section', 'code')
        verbose_name = "Документ"
        verbose_name_plural = "Документы"

    @property
    def has_active_share_link(self) -> bool:
        return bool(
            self.public_link_token
            and self.public_link_expires_at
            and self.public_link_expires_at > timezone.now()
        )

    def save(self, *args, **kwargs):
        """Не даёт менять статус/пригодность в обход FSM.

        Штатные сервисы выставляют ``_from_service = True`` после своей
        проверки. Для прямых сохранений используется создатель документа
        как пользователь перехода.
        """
        update_fields = kwargs.get('update_fields')
        touches_status = update_fields is None or bool(
            {'status', 'suitability_code'} & set(update_fields or ())
        )
        if not self._state.adding and touches_status:
            old = Document.objects.filter(pk=self.pk).values(
                'status', 'suitability_code'
            ).first()
            if old and (
                old['status'] != self.status
                or old['suitability_code'] != self.suitability_code
            ) and not getattr(self, '_from_service', False):
                from storage.domain.services import validate_transition

                user = getattr(self, '_transition_user', None) or self.created_by
                if user is None:
                    raise ValidationError(
                        "Нельзя изменить статус документа без пользователя"
                    )
                previous = copy(self)
                previous.status = old['status']
                previous.suitability_code = old['suitability_code']
                validate_transition(
                    previous,
                    self.status,
                    user,
                    self.suitability_code,
                )
        result = super().save(*args, **kwargs)
        if getattr(self, '_from_service', False):
            self._from_service = False
        return result

    def __str__(self):
        return f'{self.code} - {self.name}'


class Folder(models.Model):
    # Состояния документов (сегмент-контрол и колонка «Состояние»).
    # WIP переименован по требованию владельца: «В разработке».
    ZONE_CHOICES = [
        ('WIP', 'В разработке'),
        ('SHARED', 'На согласовании'),
        ('PUBLISHED', 'Опубликовано'),
        ('ARCHIVE', 'Архив'),
    ]
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='folders',
        verbose_name="Проект",
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='folders',
        verbose_name="Раздел",
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name="Родительская папка",
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    code = models.CharField(
        max_length=50,
        blank=True,
        default='',
        verbose_name="Код папки",
    )
    full_code = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name="Полный шифр",
    )
    order = models.IntegerField(default=0, verbose_name="Порядок сортировки")
    zone = models.CharField(
        max_length=20,
        choices=ZONE_CHOICES,
        default='WIP',
        verbose_name="Зона жизненного цикла",
    )
    is_system_zone = models.BooleanField(
        default=False,
        verbose_name="Системная папка зоны",
    )
    is_deleted = models.BooleanField(default=False, verbose_name="Удалена")
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата удаления")
    deleted_with_parent = models.BooleanField(
        default=False,
        verbose_name="Удалена вместе с родителем",
    )
    deleted_with_project = models.BooleanField(
        default=False,
        verbose_name="Удалена вместе с проектом",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_folders',
        verbose_name="Создатель",
    )

    class Meta:
        ordering = ['order', 'name']
        verbose_name = "Папка"
        verbose_name_plural = "Папки"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(parent=models.F('id')),
                name='folder_parent_not_self',
            ),
            models.UniqueConstraint(
                fields=['project', 'section', 'code'],
                condition=models.Q(code__gt='', parent__isnull=True),
                name='uniq_folder_code_per_section',
            ),
            models.UniqueConstraint(
                fields=['project', 'parent', 'code'],
                condition=models.Q(code__gt='', parent__isnull=False),
                name='uniq_folder_code_per_parent',
            ),
        ]

    def __str__(self):
        return f'{self.project.code} / {self.name}'


class DocumentVersion(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='versions', verbose_name="Документ")
    version_number = models.IntegerField(verbose_name="Номер версии")
    file = models.FileField(upload_to='documents/%Y/%m/', verbose_name="Файл")
    file_size = models.BigIntegerField(verbose_name="Размер файла")
    file_hash = models.CharField(max_length=64, blank=True, verbose_name="Хеш файла")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="Загрузил")
    # Этап 20 (треб. 3): статус ИИ-индексации версии (RAG-поиск по PDF).
    # Значения согласованы с ai_checks.domain.models.IndexingStatus:
    # pending → processing → done | error. Choices дублируются без
    # импорта из ai_checks — домен storage не зависит от ИИ-контура.
    INDEXING_STATUS_CHOICES = [
        ('pending', 'В очереди'),
        ('processing', 'Индексируется'),
        ('done', 'Готово'),
        ('error', 'Ошибка'),
    ]
    indexing_status = models.CharField(
        max_length=10,
        choices=INDEXING_STATUS_CHOICES,
        default='pending',
        verbose_name="Статус ИИ-индексации",
    )

    class Meta:
        unique_together = ('document', 'version_number')
        verbose_name = "Версия документа"
        verbose_name_plural = "Версии документов"

    def __str__(self):
        return f'v{self.version_number} - {self.document.code}'


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, verbose_name="Пользователь")
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_profiles',
        verbose_name="Компания",
    )
    ROLE_CHOICES = [
        ('admin', 'Администратор'),
        ('gip', 'ГИП'),
        ('developer', 'Разработчик'),
        ('checker', 'Проверяющий'),
        ('normocontrol', 'Нормоконтроль'),
        ('customer', 'Заказчик'),
        ('contractor', 'Смежник'),
        ('viewer', 'Наблюдатель'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, verbose_name="Роль")
    phone = models.CharField(
        max_length=30,
        blank=True,
        default='',
        verbose_name="Телефон",
    )
    allowed_sections = models.ManyToManyField(Section, blank=True, verbose_name="Доступные разделы")

    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"

    def __str__(self):
        return f'{self.user.username} - {self.role}'


class ProjectMembership(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='memberships', verbose_name="Проект")
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Пользователь")
    ROLE_IN_PROJECT_CHOICES = [
        ('gip', 'ГИП'),
        ('developer', 'Разработчик'),
        ('checker', 'Проверяющий'),
        ('normocontrol', 'Нормоконтроль'),
        ('customer', 'Заказчик'),
        ('contractor', 'Смежник'),
        ('viewer', 'Наблюдатель'),
    ]
    role_in_project = models.CharField(max_length=20, choices=ROLE_IN_PROJECT_CHOICES, verbose_name="Роль в проекте")

    class Meta:
        unique_together = ('project', 'user')
        verbose_name = "Участник проекта"
        verbose_name_plural = "Участники проектов"

    def __str__(self):
        return f'{self.user.username} - {self.project.code} ({self.role_in_project})'


class DocumentView(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='document_views',
        verbose_name="Проект",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='document_views',
        verbose_name="Пользователь",
    )
    name = models.CharField(max_length=100, verbose_name="Название вида")
    config = models.JSONField(default=dict, verbose_name="Настройки")
    is_shared = models.BooleanField(default=False, verbose_name="Общий вид")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ['name']
        verbose_name = "Вид документа"
        verbose_name_plural = "Виды документов"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(is_shared=True, user__isnull=True)
                    | models.Q(is_shared=False, user__isnull=False)
                ),
                name='documentview_shared_owner_consistency',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.project.code})'


class MailRule(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='mail_rules', verbose_name="Проект")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='mail_rules', verbose_name="Раздел")
    email_filter = models.CharField(max_length=255, verbose_name="Фильтр (email/тема)")
    doc_type = models.CharField(
        max_length=20,
        choices=Document.DOC_TYPE_CHOICES,
        default='source_data',
        verbose_name="Тип документа",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Правило почты"
        verbose_name_plural = "Правила почты"

    def __str__(self):
        return f'{self.project.code}: {self.email_filter} → {self.section.code}'


class Feedback(models.Model):
    """Обратная связь от тестировщиков/пользователей (бета)."""

    class Status(models.TextChoices):
        NEW = "NEW", "Новое"
        IN_PROGRESS = "IN_PROGRESS", "В работе"
        RESOLVED = "RESOLVED", "Решено"
        REJECTED = "REJECTED", "Отклонено"

    class Category(models.TextChoices):
        BUG = "BUG", "Ошибка"
        UX = "UX", "Неудобство"
        IDEA = "IDEA", "Идея"
        OTHER = "OTHER", "Другое"

    company = models.ForeignKey(
        "storage.Company", on_delete=models.CASCADE,
        related_name="feedback_items", verbose_name="Компания",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="feedback_items", verbose_name="Автор",
    )
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.BUG,
        verbose_name="Категория",
    )
    message = models.TextField(verbose_name="Что случилось")
    page_url = models.CharField(max_length=500, blank=True, verbose_name="Где (URL)")
    contact_email = models.EmailField(blank=True, verbose_name="Контакт для связи")
    screenshot = models.ImageField(
        upload_to="feedback/screenshots/%Y/%m/", null=True, blank=True,
        verbose_name="Скриншот",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.NEW,
        verbose_name="Статус",
    )
    admin_comment = models.TextField(blank=True, verbose_name="Комментарий админа")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Обратная связь"
        verbose_name_plural = "Обратная связь"
        indexes = [
            models.Index(fields=["company", "status", "-created_at"]),
        ]

    def __str__(self):
        return f"[{self.get_category_display()}] {self.message[:60]}"

class Specification(models.Model):
    """Строка спецификации проекта (ВОР-билдер)."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="specifications",
        verbose_name="Проект",
    )
    section_code = models.CharField(
        max_length=20, blank=True, verbose_name="Код раздела"
    )
    code = models.CharField(max_length=100, verbose_name="Код")
    name = models.CharField(max_length=255, verbose_name="Наименование")
    unit = models.CharField(max_length=20, blank=True, verbose_name="Ед. изм.")
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name="Количество"
    )
    note = models.TextField(blank=True, verbose_name="Примечание")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["section_code", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "section_code", "code"],
                name="uniq_spec_project_section_code",
            ),
        ]
        verbose_name = "Спецификация"
        verbose_name_plural = "Спецификации"

    def __str__(self):
        return f"{self.code} — {self.name}"

class VorItem(models.Model):
    """Строка ведомости объёмов работ проекта (ВОР-билдер)."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="vor_items",
        verbose_name="Проект",
    )
    section_code = models.CharField(
        max_length=20, blank=True, verbose_name="Код раздела"
    )
    code = models.CharField(max_length=100, verbose_name="Код")
    name = models.CharField(max_length=255, verbose_name="Наименование")
    unit = models.CharField(max_length=20, blank=True, verbose_name="Ед. изм.")
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name="Количество"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["section_code", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "section_code", "code"],
                name="uniq_vor_project_section_code",
            ),
        ]
        verbose_name = "Строка ВОР"
        verbose_name_plural = "Строки ВОР"

    def __str__(self):
        return f"{self.code} — {self.name}"


# =====================================================================
# Модуль «Задания» (DesignTask) — docs/DESIGN_TASKS_SPEC.md v1.0, Этап 1.
#
# Задания между смежными разделами (АР → КР, ОВ → ЭОМ и т.д.). Отличие
# от Issue: Issue — «исправь ошибку» (замечание к документу), DesignTask —
# «сделай работу» (задание от отдела-источника отделу-цели).
#
# Роль ГИПа (спека 1): ГИП не выдаёт задания вручную — он настраивает
# шаблоны, контролирует сроки и вмешивается при конфликтах. Задания
# создаются представителями профильных отделов.
#
# Бизнес-логика (матрица переходов, права, уведомления) — Этап 2,
# storage/domain/services.py. Здесь только структура данных.
# =====================================================================


class TaskTemplate(models.Model):
    """Шаблон типового задания между смежными разделами (спека 2.4).

    Настраивается ГИПом/админом на уровне компании. При создании задания
    по шаблону его checklist (JSON-массив строк) копируется в
    DesignTaskChecklistItem (Этап 2, create_task).
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='task_templates',
        verbose_name="Компания",
    )
    # Типовые источник/цель — подсказка при создании задания, поэтому
    # необязательны (null=True) и не мешают удалению раздела (SET_NULL).
    source_section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='task_templates_source',
        verbose_name="Типовой раздел-источник",
    )
    target_section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='task_templates_target',
        verbose_name="Типовой раздел-цель",
    )
    title = models.CharField(max_length=255, verbose_name="Название шаблона")
    description = models.TextField(verbose_name="Типовой текст задания")
    default_duration_days = models.PositiveIntegerField(
        default=5,
        verbose_name="Типовой срок (дней)",
    )
    # JSON-массив строк: ["Проверить нагрузки", "Сделать расчёт", ...]
    # (спека 6). default=list — у каждого шаблона свой список пунктов.
    checklist = models.JSONField(default=list, verbose_name="Чек-лист")
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        ordering = ['company', 'title']
        verbose_name = "Шаблон задания"
        verbose_name_plural = "Шаблоны заданий"
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'title'],
                name='uniq_tasktemplate_company_title',
            ),
        ]

    def __str__(self):
        return f'{self.title} ({self.company.name})'


class DesignTask(models.Model):
    """Задание между смежными разделами (спека 2.1).

    Автор — инициатор из отдела-источника: любой пользователь с
    write-доступом к source_section (проверка прав — Этап 2, сервис
    create_task через существующий check_write_permission), НЕ
    обязательно ГИП. ГИП/админ может только выдавать/принимать/отменять
    вместе с автором (матрица переходов, спека 3).
    """

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Черновик'
        ISSUED = 'ISSUED', 'Выдано'
        IN_PROGRESS = 'IN_PROGRESS', 'В работе'
        ON_HOLD = 'ON_HOLD', 'Приостановлено'
        COMPLETED = 'COMPLETED', 'Выполнено'
        REVIEW = 'REVIEW', 'На проверке'
        ACCEPTED = 'ACCEPTED', 'Принято'
        RETURNED = 'RETURNED', 'Возвращено на доработку'
        CANCELLED = 'CANCELLED', 'Отменено'

    class Priority(models.TextChoices):
        LOW = 'low', 'Низкий'
        NORMAL = 'normal', 'Обычный'
        HIGH = 'high', 'Высокий'
        CRITICAL = 'critical', 'Критический'

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='design_tasks',
        verbose_name="Проект",
    )
    # PROTECT по спеке 2.1: раздел с историей заданий нельзя молча удалить;
    # раздел удаляется только после снятия зависимостей.
    source_section = models.ForeignKey(
        Section,
        on_delete=models.PROTECT,
        related_name='design_tasks_source',
        verbose_name="Раздел-источник",
    )
    target_section = models.ForeignKey(
        Section,
        on_delete=models.PROTECT,
        related_name='design_tasks_target',
        verbose_name="Раздел-цель",
    )
    author = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='issued_design_tasks',
        verbose_name="Инициатор (отдел-источник)",
    )
    assignee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_design_tasks',
        verbose_name="Исполнитель",
    )
    template = models.ForeignKey(
        TaskTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
        verbose_name="Шаблон",
    )
    title = models.CharField(max_length=255, verbose_name="Краткое название")
    description = models.TextField(verbose_name="Суть задания")
    due_date = models.DateField(null=True, blank=True, verbose_name="Срок")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name="Статус",
    )
    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.NORMAL,
        verbose_name="Приоритет",
    )
    issued_at = models.DateTimeField(null=True, blank=True, verbose_name="Когда выдано")
    accepted_at = models.DateTimeField(null=True, blank=True, verbose_name="Когда принято")
    closed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='closed_design_tasks',
        verbose_name="Кто закрыл",
    )
    # Синхронизация с ГИП-Ассистентом (спека 7): Task/Decision из внешней
    # среды. Аналог Issue.external_id — nullable + db_index для поиска.
    external_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Внешний ID (ГИП-Ассистент)",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Задание между разделами"
        verbose_name_plural = "Задания между разделами"
        indexes = [
            # Спека 2.1: составной индекс под вкладку «Задания» проекта
            # (фильтр по проекту + связка источник→цель + сортировка).
            models.Index(
                fields=['project', 'source_section', 'target_section', 'title', 'created_at'],
                name='design_task_proj_src_tgt_idx',
            ),
        ]

    def __str__(self):
        return f'{self.source_section.code} → {self.target_section.code}: {self.title}'


class DesignTaskStatusLog(models.Model):
    """История смены статусов задания (спека 2.2).

    Пишется сервисом update_task_status (Этап 2) при каждом переходе
    по матрице. old_status/new_status — значения DesignTask.Status.
    """

    task = models.ForeignKey(
        DesignTask,
        on_delete=models.CASCADE,
        related_name='status_logs',
        verbose_name="Задание",
    )
    old_status = models.CharField(max_length=20, verbose_name="Прежний статус")
    new_status = models.CharField(max_length=20, verbose_name="Новый статус")
    changed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='design_task_status_logs',
        verbose_name="Кто изменил",
    )
    comment = models.TextField(
        blank=True,
        default='',
        verbose_name="Причина (при возврате/уточнении)",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ['created_at']
        verbose_name = "Запись истории статусов"
        verbose_name_plural = "Истории статусов заданий"

    def __str__(self):
        return f'{self.old_status} → {self.new_status} ({self.task_id})'


class DesignTaskAttachment(models.Model):
    """Вложение задания: входные данные, результат или комментарий (спека 2.3).

    Входные данные — конкретные документы (листы) проекта, результат —
    файлы исполнителя (спека 1). Заполняется через add_task_attachment
    (Этап 2): kind=input добавляет автор, kind=output — исполнитель.
    """

    class Kind(models.TextChoices):
        INPUT = 'input', 'Входные данные'
        OUTPUT = 'output', 'Результат'
        COMMENT = 'comment', 'Комментарий'

    task = models.ForeignKey(
        DesignTask,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name="Задание",
    )
    # Привязка к документу проекта. SET_NULL: документ использует
    # мягкое удаление, а жёсткое удаление (вместе с проектом) не должно
    # стирать историю задания — связь теряется, вложение и файл остаются.
    document = models.ForeignKey(
        Document,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='design_task_attachments',
        verbose_name="Документ проекта",
    )
    file = models.FileField(
        upload_to='design_tasks/attachments/%Y/%m/',
        null=True,
        blank=True,
        verbose_name="Свободный файл",
    )
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        verbose_name="Тип вложения",
    )
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='design_task_attachments',
        verbose_name="Кто загрузил",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")

    class Meta:
        ordering = ['created_at']
        verbose_name = "Вложение задания"
        verbose_name_plural = "Вложения заданий"

    def __str__(self):
        return f'{self.get_kind_display()} — {self.task_id}'


class DesignTaskChecklistItem(models.Model):
    """Пункт чек-листа задания (спека 2.5).

    Создаётся при выпуске задания по шаблону (копия TaskTemplate.checklist).
    БЕЗ уникальности по (task, text): одно задание может содержать
    несколько одинаковых пунктов (спека, Этап 1).
    """

    task = models.ForeignKey(
        DesignTask,
        on_delete=models.CASCADE,
        related_name='checklist_items',
        verbose_name="Задание",
    )
    text = models.CharField(max_length=500, verbose_name="Пункт")
    is_done = models.BooleanField(default=False, verbose_name="Выполнено")
    done_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='design_task_checklist_items',
        verbose_name="Кто выполнил",
    )
    done_at = models.DateTimeField(null=True, blank=True, verbose_name="Когда выполнил")

    class Meta:
        ordering = ['id']
        verbose_name = "Пункт чек-листа задания"
        verbose_name_plural = "Пункты чек-листа заданий"

    def __str__(self):
        return f'{self.text[:50]} ({self.task_id})'
