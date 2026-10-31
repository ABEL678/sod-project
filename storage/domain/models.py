from django.db import models
from django.contrib.auth.models import User


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
    public_link_token = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        verbose_name="Публичный токен",
    )
    is_deleted = models.BooleanField(default=False, verbose_name="Удален")
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата удаления")
    deleted_with_parent = models.BooleanField(
        default=False,
        verbose_name="Удален вместе с родителем",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="Создатель")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        unique_together = ('project', 'section', 'code')
        verbose_name = "Документ"
        verbose_name_plural = "Документы"

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
    is_deleted = models.BooleanField(default=False, verbose_name="Удалена")
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата удаления")
    deleted_with_parent = models.BooleanField(
        default=False,
        verbose_name="Удалена вместе с родителем",
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
