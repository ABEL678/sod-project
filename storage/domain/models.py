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
    SUITABILITY_CHOICES = [
        ('S0', 'В работе'),
        ('S1', 'Для координации'),
        ('S2', 'Для ознакомления'),
        ('S3', 'На согласовании'),
        ('S4', 'На утверждении'),
        ('A', 'Утверждено'),
        ('B', 'Утверждено с замечаниями'),
        ('CR', 'Исполнительная'),
    ]
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
    ZONE_CHOICES = [
        ('WIP', 'В работе'),
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
