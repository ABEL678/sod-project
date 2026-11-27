from copy import copy

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
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


class Customer(models.Model):
    """Карточка заказчика (организации) в пределах компании-владельца СОД."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='customers',
        verbose_name="Компания",
    )
    name = models.CharField(max_length=255, verbose_name="Наименование")

    class Meta:
        verbose_name = "Заказчик"
        verbose_name_plural = "Заказчики"
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'name'],
                name='uniq_customer_name_per_company',
            ),
        ]

    def __str__(self):
        return self.name


class Project(models.Model):
    STAGE_P = 'P'
    STAGE_RD = 'RD'
    STAGE_CHOICES = [
        (STAGE_P, 'П'),
        (STAGE_RD, 'РД'),
    ]
    OBJECT_TYPE_CHOICES = [
        ('residential', 'Жилое'),
        ('public', 'Общественное'),
        ('industrial', 'Производственное'),
        ('linear', 'Линейное'),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='projects', verbose_name="Компания")
    code = models.CharField(max_length=50, unique=True, verbose_name="Шифр")
    base_code = models.CharField(
        max_length=50,
        blank=True,
        default='',
        verbose_name="Базовое обозначение",
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    customer_org = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='projects',
        verbose_name="Заказчик",
    )
    stage = models.CharField(
        max_length=2,
        choices=STAGE_CHOICES,
        null=True,
        blank=True,
        default=None,
        verbose_name="Стадия",
    )
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
    object_type = models.CharField(
        max_length=20,
        choices=OBJECT_TYPE_CHOICES,
        null=True,
        blank=True,
        default=None,
        verbose_name="Тип объекта",
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

    @property
    def customer(self) -> str:
        """Наименование заказчика для API и отображения (вычисляется из карточки)."""
        return self.customer_org.name if self.customer_org_id else ''

    @property
    def gip(self):
        """Действующий ГИП проекта из состава команды (или None).

        Если состав предзагружен (prefetch_related('memberships')),
        читаем кэш без запроса — так топбар не дёргает БД на каждое
        обращение к свойству.
        """
        prefetched = getattr(
            self, '_prefetched_objects_cache', {},
        ).get('memberships')
        if prefetched is None:
            membership = (
                self.memberships.select_related('user')
                .filter(role_in_project='gip')
                .order_by('-id')
                .first()
            )
        else:
            membership = max(
                (
                    item for item in prefetched
                    if item.role_in_project == 'gip'
                ),
                key=lambda item: item.pk,
                default=None,
            )
        return membership.user if membership else None

    @property
    def gip_id(self):
        user = self.gip
        return user.pk if user else None


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
    # Единое главное состояние документа (MASTER_SPEC_2_0, волна 0 п. 1).
    # Статус, код пригодности и зона — вычисляются из состояния и не
    # хранятся. Шесть значений: четыре внутренних статуса работы,
    # «на согласовании», «утверждён», «исполнительная документация»,
    # «архив» (последние три — зоны PUBLISHED/ARCHIVE и исполнительная).
    STATE_WIP_DRAFT = 'wip_draft'
    STATE_WIP_INTERNAL_REVIEW = 'wip_internal_review'
    STATE_WIP_REWORK = 'wip_rework'
    STATE_WIP_READY = 'wip_ready'
    STATE_SHARED = 'shared'
    STATE_APPROVED = 'approved'
    # Путь перехода в исполнительную документацию отложен во 2-ю волну;
    # тестом не закрыт (SPEC 2.0, волна 0: состояние объявлено заранее).
    STATE_AS_BUILT = 'as_built'
    STATE_ARCHIVED = 'archived'
    STATE_CHOICES = [
        (STATE_WIP_DRAFT, 'В работе'),
        (STATE_WIP_INTERNAL_REVIEW, 'На внутренней проверке'),
        (STATE_WIP_REWORK, 'На доработке'),
        (STATE_WIP_READY, 'Готов к передаче'),
        (STATE_SHARED, 'На согласовании'),
        (STATE_APPROVED, 'Утверждён'),
        (STATE_AS_BUILT, 'Исполнительная документация'),
        (STATE_ARCHIVED, 'Архив'),
    ]
    state = models.CharField(
        max_length=24,
        choices=STATE_CHOICES,
        default=STATE_WIP_DRAFT,
        verbose_name="Состояние",
    )
    # Ручной результат последней проверки (S1/S2/S4/B-подобные отметки).
    # Не вычисляется из состояния: вводится вручную. Ручной вердикт действует
    # до утверждения: сопровождает документ через SHARED, фиксируется в
    # записи одобрения, очищается при входе в approved/as_built
    # (решение владельца 25.11.2026; порядок — в services._apply_state).
    REVIEW_RESULT_CHOICES = [
        ('', 'Не задан'),
        ('S1', 'Для совместной работы'),
        ('S2', 'Для ознакомления'),
        ('S4', 'На утверждении'),
    ]
    # Коды ручного вердикта проверки: хранятся в review_result, а не в
    # проекции состояния (исключение из правила «всё вычисляется»).
    MANUAL_REVIEW_CODES = ('S1', 'S2', 'S4')
    review_result = models.CharField(
        max_length=2,
        choices=REVIEW_RESULT_CHOICES,
        blank=True,
        default='',
        verbose_name="Результат последней проверки",
    )

    # Переопределение итога согласования (SPEC 2.0, волна 0, п. 6):
    # «B вопреки блокирующим» — только ГИП проекта, с обоснованием.
    # Действует до появления нового блокирующего замечания; каждая
    # установка пишется в ChangeSetEntry ('result_override').
    outcome_override = models.BooleanField(
        default=False,
        verbose_name="Итог переопределён ГИПом",
    )
    outcome_override_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name="Кто переопределил итог",
    )
    outcome_override_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Когда переопределён итог",
    )
    outcome_override_text = models.TextField(
        blank=True,
        default='',
        verbose_name="Обоснование переопределения итога",
    )

    # Проекция состояния на прежние признаки (только чтение / вычисление).
    # Соответствие — таблица STATE_PROJECTION ниже; при записи сеттеры
    # переводят состояние, а не хранят признак отдельно.
    STATUS_CHOICES = [
        ('draft', 'Черновик'),
        ('in_review', 'На проверке'),
        ('approved', 'Утверждён'),
        ('archived', 'Архив'),
    ]
    SUITABILITY_CHOICES = [
        ('S0', 'Черновик'),
        ('S1', 'Для совместной работы'),
        ('S2', 'Для ознакомления'),
        ('S3', 'На проверке'),
        ('S4', 'На утверждении'),
        ('A', 'Утверждён'),
        ('B', 'Утверждено с замечаниями'),
    ]
    SUITABILITY_DESCRIPTIONS = {
        'S0': 'Документ в разработке, не передаётся другим подразделениям.',
        'S1': 'Документ передан смежным отделам для учёта в их работе.',
        'S2': 'Документ передан для сведения, изменения по нему не вносятся.',
        'S3': 'Документ проверяют ГИП, проверяющий или нормоконтролёр.',
        'S4': 'Документ передан Заказчику для утверждения.',
        'A': 'Документ утверждён Заказчиком, допущен к использованию.',
        'B': 'Документ утверждён Заказчиком с замечаниями.',
    }
    INTERNAL_STATUS_CHOICES = [
        ('draft', 'В работе'),
        ('internal_review', 'На внутренней проверке'),
        ('rework', 'На доработке'),
        ('ready_to_share', 'Готов к передаче'),
    ]

    # Соответствие состояния прежним признакам: (status, suitability,
    # internal_status, зона). Зона — каноническая зона папки.
    STATE_PROJECTION = {
        STATE_WIP_DRAFT: ('draft', 'S0', 'draft', 'WIP'),
        STATE_WIP_INTERNAL_REVIEW: ('draft', 'S0', 'internal_review', 'WIP'),
        STATE_WIP_REWORK: ('draft', 'S0', 'rework', 'WIP'),
        STATE_WIP_READY: ('draft', 'S0', 'ready_to_share', 'WIP'),
        STATE_SHARED: ('in_review', 'S3', 'draft', 'SHARED'),
        STATE_APPROVED: ('approved', 'A', 'draft', 'PUBLISHED'),
        STATE_AS_BUILT: ('approved', 'A', 'draft', 'PUBLISHED'),
        STATE_ARCHIVED: ('archived', 'A', 'draft', 'ARCHIVE'),
    }

    @classmethod
    def state_from_projection(cls, status, suitability, internal_status):
        """Состояние по прежним признакам; None, если пара не сопоставима."""
        if status == 'draft' and suitability == 'S0' and internal_status in (
            'draft', 'internal_review', 'rework', 'ready_to_share',
        ):
            return {
                'draft': cls.STATE_WIP_DRAFT,
                'internal_review': cls.STATE_WIP_INTERNAL_REVIEW,
                'rework': cls.STATE_WIP_REWORK,
                'ready_to_share': cls.STATE_WIP_READY,
            }[internal_status]
        if status == 'in_review' and suitability == 'S3':
            return cls.STATE_SHARED
        if status == 'approved' and suitability == 'A':
            return cls.STATE_APPROVED
        if status == 'archived' and suitability == 'A':
            return cls.STATE_ARCHIVED
        return None

    def _projection(self):
        return self.STATE_PROJECTION[self.state]

    @property
    def status(self) -> str:
        return self._projection()[0]

    @status.setter
    def status(self, value):
        self._set_projection(status=value)

    @property
    def suitability_code(self) -> str:
        """Код пригодности: ручной вердикт в зоне WIP, иначе проекция.

        Исключение из правила «всё вычисляется» (решение владельца):
        ручной вердикт ``review_result`` читается, пока документ находится
        в зоне проверки WIP. Вердикт не переживает выход из проверки —
        вне WIP возвращается проекция состояния.
        """
        if self.state.startswith('wip_') and self.review_result:
            return self.review_result
        return self._projection()[1]

    @suitability_code.setter
    def suitability_code(self, value):
        """Единственный санкционированный канал записи review_result.

        Ручной вердикт проверки (S1/S2/S4) пишется только здесь и состояние
        не меняет: смена ``review_result`` — не переход состояния, страж
        ``Document.save()`` для неё не применяется. Остальные коды задают
        группу состояния.
        """
        if value in self.MANUAL_REVIEW_CODES:
            self.review_result = value
            return
        if value in ('S0', 'S3', 'A', 'B'):
            self.review_result = ''
        if value == 'S0':
            self.state = (
                self.state if self.state.startswith('wip_')
                else self.STATE_WIP_DRAFT
            )
        elif value == 'S3':
            self.state = self.STATE_SHARED
        elif value in ('A', 'B'):
            self.state = self.STATE_APPROVED
        # Неизвестный код не меняет состояние (совместимость с прежним CharField).

    @property
    def internal_status(self) -> str:
        return self._projection()[2]

    @internal_status.setter
    def internal_status(self, value):
        self._set_projection(internal_status=value)

    def _set_projection(self, status=None, suitability=None, internal_status=None):
        """Задаёт состояние по признаку (совместимость с прежним API).

        Признак задаёт состояние по умолчанию для своей группы: статус
        ``draft`` — рабочее состояние (с внутренним статусом текущего),
        ``in_review`` — на согласовании, ``approved`` — утверждён,
        ``archived`` — архив. Пригодность и внутренний статус — только
        вычисляются, поэтому несовпадающая пара не отвергается, а
        нормализуется к состоянию группы.
        """
        cur_status, _, cur_internal, _ = self._projection()
        if internal_status is not None and status is None:
            status = 'draft'
        new_status = status if status is not None else cur_status
        if new_status == 'draft':
            internal = internal_status if internal_status is not None else cur_internal
            new = self.state_from_projection('draft', 'S0', internal)
            if new is None:
                new = self.STATE_WIP_DRAFT
        elif new_status == 'in_review':
            new = self.STATE_SHARED
        elif new_status == 'approved':
            new = self.STATE_APPROVED
        elif new_status == 'archived':
            new = self.STATE_ARCHIVED
        else:
            # Прежнее поле статуса принимало любую строку (CharField без
            # проверки при создании). Для совместимости неизвестный статус
            # не меняет состояние; строгая проверка — в сервисах переходов.
            return
        self.state = new

    def get_status_display(self) -> str:
        return dict(self.STATUS_CHOICES).get(self.status, self.status)

    def get_suitability_code_display(self) -> str:
        return dict(self.SUITABILITY_CHOICES).get(
            self.suitability_code, self.suitability_code
        )

    def get_internal_status_display(self) -> str:
        return dict(self.INTERNAL_STATUS_CHOICES).get(
            self.internal_status, self.internal_status
        )

    def get_state_display(self) -> str:
        return dict(self.STATE_CHOICES).get(self.state, self.state)

    @property
    def zone(self) -> str:
        """Зона по состоянию (папка зоны учитывается в services._effective_zone)."""
        return self._projection()[3]
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
    # Аннулирование документа по РВИ (SPEC 2.0, волна 4, W4-2).
    # Отличается от мягкого удаления (корзина): документ остаётся в
    # истории проекта, но помечен как аннулированный и не принимает
    # новые версии. Основание — утверждённое РВИ с записью
    # «аннулировать» в содержании изменения.
    annulled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Дата аннулирования",
    )
    annulled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='annulled_documents',
        verbose_name="Кто аннулировал",
    )
    annulment_permission = models.ForeignKey(
        'ChangePermission',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='annulled_documents',
        verbose_name="Основание аннулирования (РВИ)",
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

    @property
    def is_annulled(self) -> bool:
        """Документ аннулирован по утверждённому РВИ."""
        return self.annulled_at is not None

    def save(self, *args, **kwargs):
        """Страж: состояние меняется только через переход (сервис).

        Изменение ``state`` (а значит, статуса, пригодности, внутреннего
        статуса и зоны — все вычисляются из него) без ``_from_service = True``
        проверяется машиной переходов ``validate_transition``. Страж
        блокирует смену ``state`` без ``_from_service``; смена только
        ``folder`` — легальна (перемещение внутри зоны, решение владельца
        25.11.2026).

        Смена ``review_result`` легитимна в обход стража: это не переход
        состояния, а ручной вердикт проверки (санкционированный канал —
        сеттер ``suitability_code``).
        """
        update_fields = kwargs.get('update_fields')
        touches_state = update_fields is None or bool(
            {'state', 'folder'} & set(update_fields or ())
        )
        if not self._state.adding and touches_state:
            old = Document.objects.filter(pk=self.pk).values(
                'state', 'folder_id'
            ).first()
            changed_state = bool(old) and old['state'] != self.state
            changed_folder = bool(old) and old['folder_id'] != self.folder_id
            if (changed_state or changed_folder) and not getattr(
                self, '_from_service', False
            ):
                from storage.domain.services import validate_transition

                user = getattr(self, '_transition_user', None) or self.created_by
                if user is None:
                    raise ValidationError(
                        "Нельзя изменить состояние документа без пользователя"
                    )
                previous = copy(self)
                previous.state = old['state']
                status, suitability, _, _ = Document.STATE_PROJECTION[self.state]
                validate_transition(previous, status, user, suitability)
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
        verbose_name="Этап жизненного цикла",
    )
    is_system_zone = models.BooleanField(
        default=False,
        verbose_name="Системная папка этапа",
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


# Причина новой версии документа (SPEC 2.0, волна 0, п. 5).
# issue/task/rfi требуют полиморфной ссылки на источник; other — текста.
# Тип «исходные данные» появится в волне 1 вместе с реестром ИД.
VERSION_REASON_CHOICES = [
    ('issue', 'Замечание'),
    ('task', 'Задание'),
    ('rfi', 'Запрос на информацию'),
    ('other', 'Другое'),
]


class DocumentVersion(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='versions', verbose_name="Документ")
    version_number = models.IntegerField(verbose_name="Номер версии")
    file = models.FileField(upload_to='documents/%Y/%m/', verbose_name="Файл")
    file_size = models.BigIntegerField(verbose_name="Размер файла")
    file_hash = models.CharField(max_length=64, blank=True, verbose_name="Хеш файла")
    # «Состав изменений» — что именно поменялось в этой версии.
    comment = models.TextField(blank=True, verbose_name="Состав изменений")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="Загрузил")
    # Причина версии (SPEC 2.0, волна 0, п. 5). Для issue/task/rfi
    # обязательна ссылка на источник (content_type + object_id), для
    # other — непустой reason_text. «Исходные данные» — волна 1.
    reason_type = models.CharField(
        max_length=10,
        choices=VERSION_REASON_CHOICES,
        default='other',
        verbose_name="Тип причины версии",
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Тип источника причины",
    )
    object_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="ID источника причины",
    )
    reason = GenericForeignKey('content_type', 'object_id')
    reason_text = models.TextField(
        blank=True,
        default='',
        verbose_name="Причина версии (текст)",
    )
    # Основание версии — утверждённое РВИ (SPEC 2.0, волна 4, W4-2).
    # Заполняется только upload_document() при расходовании изменения:
    # номер изменения (графа 4) берётся из связи РВИ↔документ и
    # фиксируется в версии для таблицы изменений основной надписи.
    change_permission = models.ForeignKey(
        'ChangePermission',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='versions',
        verbose_name="Основание изменения (РВИ)",
    )
    change_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Номер изменения",
    )
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

    def get_reason_object(self):
        """Источник причины или None (объект мог быть удалён)."""
        try:
            return self.reason
        except ObjectDoesNotExist:
            return None


# Журнал изменений версии (SPEC 2.0, волна 0, п. 8).
# version_release — выпуск версии; result_override — переопределение
# итога согласования ГИПом (п. 6); approval — запись об одобрении (п. 7,
# добавляется отдельным шагом).
CHANGESET_ENTRY_TYPES = [
    ('version_release', 'Выпуск версии'),
    ('result_override', 'Переопределение итога согласования'),
    ('approval', 'Одобрение документа'),
]


class ChangeSetEntry(models.Model):
    """Запись журнала изменений версии документа.

    Назначение — аудиторский след выпуска версии и опора для разрешения
    на внесение изменений (форма 9 приложения Л ГОСТ Р 21.101-2026,
    волна 2, п. 5.4): состав изменений, затронутые листы, автор и дата.

    Правило источников: причина версии в запись НЕ дублируется — запись
    читает ``version.reason_type`` / ``version.reason`` /
    ``version.reason_text``, а состав изменений — ``version.comment``.
    Одно поле — один источник.

    Поле ``affected_sheets`` — свободный текст («листы 3, 7–9»), только
    для записей типа ``version_release``; строгая валидация номеров
    появится в волне 2 (п. 4.2), когда листы станут сущностью.

    План расширения: записи п. 6 (переходы и итог согласования) и п. 7
    (одобрения) лягут в этот же журнал через ``entry_type``; текст
    решения/одобрения — в ``text``.
    """

    version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.CASCADE,
        related_name='changeset_entries',
        verbose_name="Версия документа",
    )
    entry_type = models.CharField(
        max_length=20,
        choices=CHANGESET_ENTRY_TYPES,
        default='version_release',
        verbose_name="Тип записи",
    )
    text = models.TextField(
        blank=True,
        default='',
        verbose_name="Текст записи",
    )
    affected_sheets = models.TextField(
        blank=True,
        default='',
        verbose_name="Затронутые листы",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='changeset_entries',
        verbose_name="Автор записи",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Дата записи",
    )

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name = "Запись изменений версии"
        verbose_name_plural = "Записи изменений версий"

    def __str__(self):
        return (
            f'{self.get_entry_type_display()} — '
            f'v{self.version.version_number} {self.version.document.code}'
        )


# Разрешение на внесение изменений (РВИ), SPEC 2.0, волна 4.
# Формы 9 и 9а приложения К ГОСТ Р 21.101-2026. Наименования причин —
# по таблице К.1 (графа 7); пустой код = прочерк в графе.
CHANGE_PERMISSION_REASON_CHOICES = [
    ('1', 'Усовершенствования'),
    ('2', 'Изменение стандартов и норм'),
    ('3', 'Дополнительные требования заказчика'),
    ('4', 'Устранение ошибок'),
    ('5', 'Другие причины'),
]


class ChangePermission(models.Model):
    """Разрешение на внесение изменений (РВИ).

    Черновик редактируется ГИПом и не влияет на действия. При первом
    утверждении реестр организации присваивает сквозной ``code``;
    утверждённое РВИ неизменяемо (опечатка аннулируется выпуском нового
    разрешения). Связь с документами — через ``ChangePermissionDocument``.
    """

    STATUS_DRAFT = 'draft'
    STATUS_APPROVED = 'approved'
    STATUS_ANNULLED = 'annulled'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_APPROVED, 'Утверждено'),
        (STATUS_ANNULLED, 'Аннулировано'),
    ]

    code = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name="Обозначение РВИ",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='change_permissions',
        verbose_name="Проект",
    )
    organization = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='change_permissions',
        verbose_name="Организация",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        verbose_name="Статус",
    )
    content = models.TextField(verbose_name="Содержание изменения")
    reason_code = models.CharField(
        max_length=1,
        choices=CHANGE_PERMISSION_REASON_CHOICES,
        blank=True,
        default='',
        verbose_name="Код причины",
    )
    note = models.TextField(
        blank=True,
        default='',
        verbose_name="Примечание",
    )
    author = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='authored_change_permissions',
        verbose_name="Составил",
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_change_permissions',
        verbose_name="Утвердил",
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Дата утверждения",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Дата создания",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Дата обновления",
    )

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name = "Разрешение на внесение изменений"
        verbose_name_plural = "Разрешения на внесение изменений"
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'code'],
                condition=models.Q(code__isnull=False),
                name='uniq_change_permission_code_per_org',
            ),
        ]

    def __str__(self):
        return self.code or f'РВИ-черновик #{self.pk}'

    def save(self, *args, **kwargs):
        """Утверждённое/аннулированное РВИ неизменяемо.

        Единственное разрешённое изменение — перевод утверждённого РВИ в
        «аннулировано» через ``allow_annulment=True`` (это делает сервис
        ``annul_change_permission``). Обозначение из реестра при этом
        сохраняется: номер не переиспользуется.
        """
        allow_annulment = kwargs.pop('allow_annulment', False)
        if self.project_id and self.organization_id:
            if self.project.company_id != self.organization_id:
                raise ValidationError(
                    "Организация РВИ должна совпадать с компанией проекта"
                )
        if not self._state.adding:
            old = (
                ChangePermission.objects
                .filter(pk=self.pk)
                .values(
                    'status', 'code', 'project_id', 'organization_id',
                    'content', 'reason_code', 'note', 'author_id',
                    'approved_by_id', 'approved_at', 'created_at',
                )
                .first()
            )
            if old is not None and old['status'] in (
                self.STATUS_APPROVED, self.STATUS_ANNULLED,
            ):
                immutable_fields = (
                    'code', 'project_id', 'organization_id', 'content',
                    'reason_code', 'note', 'author_id', 'approved_by_id',
                    'approved_at', 'created_at',
                )
                changed = any(
                    getattr(self, field) != old[field]
                    for field in immutable_fields
                )
                allowed_annulment = (
                    allow_annulment
                    and old['status'] == self.STATUS_APPROVED
                    and self.status == self.STATUS_ANNULLED
                    and not changed
                )
                if not allowed_annulment:
                    raise ValidationError(
                        "Утверждённое разрешение на внесение изменений "
                        "неизменяемо"
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status in (self.STATUS_APPROVED, self.STATUS_ANNULLED):
            raise ValidationError(
                "Утверждённое разрешение на внесение изменений "
                "нельзя удалить"
            )
        return super().delete(*args, **kwargs)


class ChangePermissionDocument(models.Model):
    """Связь РВИ с документом: номер изменения и список листов.

    ``applied_at`` — факт расходования изменения. Расход = одно действие:
    вывод документа из архива ИЛИ загрузка новой версии, зафиксированный
    в ``applied_action``. Выбранная семантика (W4-2): вывод из архива
    расходует изменение сразу, а последующая загрузка версии по тому же
    РВИ допускается без повторного расхода — иначе ГИП не смог бы
    вернуть документ и внести изменение одним разрешением. Аннулирование
    документа расходует изменение отдельным действием.
    Утверждённая связь допускает только заполнение ``applied_at`` и
    ``applied_action``; остальные поля неизменяемы.
    """

    APPLIED_BY_VERSION = 'version'
    APPLIED_BY_UNARCHIVE = 'unarchive'
    APPLIED_BY_ANNULMENT = 'annulment'
    APPLIED_ACTION_CHOICES = [
        (APPLIED_BY_VERSION, 'Загрузка версии'),
        (APPLIED_BY_UNARCHIVE, 'Вывод из архива'),
        (APPLIED_BY_ANNULMENT, 'Аннулирование документа'),
    ]

    permission = models.ForeignKey(
        ChangePermission,
        on_delete=models.CASCADE,
        related_name='document_links',
        verbose_name="Разрешение",
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='change_permission_links',
        verbose_name="Документ",
    )
    change_number = models.PositiveIntegerField(
        verbose_name="Номер изменения",
    )
    sheets = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name="Листы",
    )
    applied_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Изменение израсходовано",
    )
    applied_action = models.CharField(
        max_length=10,
        choices=APPLIED_ACTION_CHOICES,
        blank=True,
        default='',
        verbose_name="Чем израсходовано",
    )

    class Meta:
        unique_together = ('permission', 'document')
        verbose_name = "Документ разрешения на изменения"
        verbose_name_plural = "Документы разрешений на изменения"

    def __str__(self):
        return (
            f'{self.permission_id} → {self.document.code} '
            f'(изм. {self.change_number})'
        )

    def save(self, *args, **kwargs):
        if self._state.adding:
            if not ChangePermission.objects.filter(
                pk=self.permission_id,
                status=ChangePermission.STATUS_DRAFT,
            ).exists():
                raise ValidationError(
                    "Добавлять документы можно только в черновик РВИ"
                )
        else:
            old = (
                ChangePermissionDocument.objects
                .filter(pk=self.pk)
                .values(
                    'permission_id', 'document_id', 'change_number',
                    'sheets', 'applied_at',
                )
                .first()
            )
            if old is not None and ChangePermission.objects.filter(
                pk=old['permission_id'],
                status__in=(
                    ChangePermission.STATUS_APPROVED,
                    ChangePermission.STATUS_ANNULLED,
                ),
            ).exists():
                update_fields = kwargs.get('update_fields')
                allowed_applied_fields = (
                    {'applied_at'}, {'applied_at', 'applied_action'},
                )
                if (
                    update_fields is None
                    or set(update_fields) not in allowed_applied_fields
                    or old['permission_id'] != self.permission_id
                    or old['document_id'] != self.document_id
                    or old['change_number'] != self.change_number
                    or old['sheets'] != self.sheets
                ):
                    raise ValidationError(
                        "Связи утверждённого разрешения на внесение "
                        "изменений неизменяемы"
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.permission.status in (
            ChangePermission.STATUS_APPROVED,
            ChangePermission.STATUS_ANNULLED,
        ):
            raise ValidationError(
                "Связи утверждённого разрешения на внесение изменений "
                "нельзя удалить"
            )
        return super().delete(*args, **kwargs)


class ChangePermissionStatusLog(models.Model):
    """История статусов РВИ: черновик → утверждено (→ аннулировано).

    Протокольная запись для карточки РВИ. Изменяется только добавлением;
    строки не редактируются и не удаляются.
    """

    permission = models.ForeignKey(
        ChangePermission,
        on_delete=models.CASCADE,
        related_name='status_logs',
        verbose_name="Разрешение",
    )
    old_status = models.CharField(
        max_length=20,
        blank=True,
        default='',
        verbose_name="Прежний статус",
    )
    new_status = models.CharField(
        max_length=20,
        verbose_name="Новый статус",
    )
    changed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        verbose_name="Кто изменил",
    )
    comment = models.TextField(
        blank=True,
        default='',
        verbose_name="Комментарий",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Дата изменения",
    )

    class Meta:
        ordering = ['created_at', 'pk']
        verbose_name = "Запись истории статуса РВИ"
        verbose_name_plural = "История статусов РВИ"

    def __str__(self):
        return (
            f'{self.permission_id}: {self.old_status or "—"} → '
            f'{self.new_status}'
        )


class ChangePermissionRegistry(models.Model):
    """Сквозной счётчик обозначений РВИ в рамках организации.

    Реестр — журнал регистрации разрешений (ГОСТ Р 21.1003-2009).
    Блокировка строки реестра сериализует выдачу номеров.
    """

    organization = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name='change_permission_registry',
        verbose_name="Организация",
    )
    last_number = models.PositiveIntegerField(
        default=0,
        verbose_name="Последний номер",
    )

    class Meta:
        verbose_name = "Реестр разрешений на изменения"
        verbose_name_plural = "Реестры разрешений на изменения"

    def __str__(self):
        return f'{self.organization}: {self.last_number}'


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
        ('manager', 'Руководитель'),
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
        constraints = [
            models.UniqueConstraint(
                fields=['project'],
                condition=models.Q(role_in_project='gip'),
                name='uniq_active_gip_per_project',
            ),
        ]

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


class RfiTask(models.Model):
    """Запрос внешних ИД у Заказчика (RFI).

    Отдельная сущность, не флаг на DesignTask (решение D1).
    Статусы строкой совпадают 1:1 с RfiState ГИП-Ассистента (D2).
    """

    STATUS_CHOICES = [
        ('open', 'Открыт'),
        ('sent', 'Отправлен'),
        ('answered', 'Ответ получен'),
        ('registered', 'ИД зарегистрированы'),
        ('closed', 'Закрыт'),
        ('escalated', 'Эскалирован'),
    ]

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='rfi_tasks',
        verbose_name="Проект",
    )
    number = models.PositiveIntegerField(verbose_name="Номер RFI")
    subject = models.CharField(max_length=255, verbose_name="Тема запроса")
    reason = models.TextField(verbose_name="Суть запроса")
    author = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='created_rfi_tasks',
        verbose_name="Автор",
    )
    assignee = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_rfi_tasks', verbose_name="Ответственный",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='open', db_index=True,
        verbose_name="Статус",
    )
    rfi_due_date = models.DateField(
        null=True, blank=True, verbose_name="Срок ответа",
    )
    document_request = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rfi_as_request', verbose_name="Документ запроса",
    )
    document_response = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rfi_as_response', verbose_name="Документ ответа",
    )
    external_id = models.CharField(
        max_length=100, null=True, blank=True, db_index=True,
        verbose_name="External ID (ГИП-Ассистент)",
    )
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name="Отправлен")
    answered_at = models.DateTimeField(null=True, blank=True, verbose_name="Ответ получен")
    registered_at = models.DateTimeField(null=True, blank=True, verbose_name="ИД зарегистрированы")
    escalated_at = models.DateTimeField(null=True, blank=True, verbose_name="Эскалирован")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Закрыт")
    closed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='closed_rfi_tasks', verbose_name="Кто закрыл",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")

    class Meta:
        unique_together = ('project', 'number')
        indexes = [
            models.Index(fields=['project', 'status']),
            models.Index(fields=['project', 'rfi_due_date']),
        ]
        ordering = ['-number']
        verbose_name = "Запрос информации (RFI)"
        verbose_name_plural = "Запросы информации (RFI)"

    def __str__(self):
        return f'RFI-{self.number:02d}: {self.subject[:50]}'


class RfiStatusLog(models.Model):
    """История смены статусов RFI (по образцу DesignTaskStatusLog)."""

    rfi = models.ForeignKey(
        RfiTask, on_delete=models.CASCADE, related_name='status_logs',
        verbose_name="RFI",
    )
    old_status = models.CharField(max_length=20, verbose_name="Прежний статус")
    new_status = models.CharField(max_length=20, verbose_name="Новый статус")
    changed_by = models.ForeignKey(
        User, on_delete=models.PROTECT,
        related_name='rfi_status_logs', verbose_name="Кто изменил",
    )
    comment = models.TextField(blank=True, default='', verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")

    class Meta:
        ordering = ['created_at']
        verbose_name = "Запись истории статусов RFI"
        verbose_name_plural = "Истории статусов RFI"

    def __str__(self):
        return f'{self.old_status} → {self.new_status} (RFI-{self.rfi_id})'


class SyncState(models.Model):
    """Состояние S3-синхронизации проекта (S3_SYNC_PLAN.md §6.3)."""

    project = models.OneToOneField(
        Project, on_delete=models.CASCADE, related_name='sync_state',
        verbose_name="Проект",
    )
    dirty = models.BooleanField(default=True, verbose_name="Требует выгрузки")
    enabled = models.BooleanField(default=True, verbose_name="Синхронизация включена")
    generation = models.PositiveIntegerField(default=0, verbose_name="Поколение выгрузки")
    last_success_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Последняя успешная выгрузка",
    )
    last_full_sync_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Полная выгрузка (все проекты)",
    )
    last_error = models.TextField(blank=True, default='', verbose_name="Последняя ошибка")
    consecutive_failures = models.PositiveIntegerField(default=0, verbose_name="Сбой подряд")
    last_inbox_seq = models.PositiveIntegerField(
        default=0, verbose_name="Посл. обраб. seq inbox",
    )
    last_ack_generation = models.PositiveIntegerField(
        default=0, verbose_name="Подтвержд. поколение",
    )
    last_ack_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Последнее подтверждение",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")

    class Meta:
        verbose_name = "Состояние S3-синхронизации"
        verbose_name_plural = "Состояния S3-синхронизации"

    def __str__(self):
        return f'SyncState({self.project.code}, gen={self.generation})'


class SyncLog(models.Model):
    """Лог S3-синхронизации (outbox/inbox)."""

    class Direction(models.TextChoices):
        OUT = "out", "Выгрузка"
        IN = "in", "Загрузка"

    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARN = "warn", "Warning"
        ERROR = "error", "Error"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='sync_logs',
        verbose_name="Проект",
    )
    direction = models.CharField(
        max_length=3, choices=Direction.choices, verbose_name="Направление",
    )
    level = models.CharField(
        max_length=5, choices=Level.choices, default=Level.INFO,
        verbose_name="Уровень",
    )
    generation = models.PositiveIntegerField(default=0, verbose_name="Поколение")
    message = models.CharField(max_length=500, verbose_name="Сообщение")
    detail = models.JSONField(null=True, blank=True, verbose_name="Детали")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")

    class Meta:
        indexes = [
            models.Index(fields=['project', '-created_at']),
            models.Index(fields=['direction', 'level']),
        ]
        ordering = ['-created_at']
        verbose_name = "Лог S3-синхронизации"
        verbose_name_plural = "Логи S3-синхронизации"

    def __str__(self):
        return f'{self.direction}/{self.level} gen={self.generation}: {self.message[:60]}'


class InboxCommand(models.Model):
    """Команда из S3-бакета (inbox/commands/{seq}_{type}_{cid}.json)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает обработки"
        IN_PROGRESS = "in_progress", "В обработке"
        DONE = "done", "Выполнена"
        FAILED = "failed", "Ошибка"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='inbox_commands',
        verbose_name="Проект",
    )
    command_id = models.CharField(
        max_length=255, verbose_name="Идентификатор команды",
    )
    seq = models.PositiveIntegerField(verbose_name="Номер команды")
    type = models.CharField(max_length=50, verbose_name="Тип команды")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
        verbose_name="Статус обработки",
    )
    payload = models.JSONField(
        null=True, blank=True, verbose_name="Тело команды",
    )
    error = models.TextField(blank=True, default='', verbose_name="Ошибка")
    file_key = models.CharField(
        max_length=500, blank=True, default='', verbose_name="Ключ файла",
    )
    received_at = models.DateTimeField(
        auto_now_add=True, verbose_name="Получен",
    )
    processed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Обработан",
    )

    class Meta:
        unique_together = ('project', 'command_id')
        ordering = ['seq']
        verbose_name = "Inbox-команда"
        verbose_name_plural = "Inbox-команды"

    def __str__(self):
        return f'Inbox({self.project.code}, seq={self.seq}, {self.type})'
