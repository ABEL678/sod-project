from django.contrib.auth.models import User
from django.db import models
from django.db.models import CheckConstraint, Q

from storage.domain.models import Document, DocumentVersion, Project


class Issue(models.Model):
    STATUS_CHOICES = [
        ('NEW', 'Новое'),
        ('IN_PROGRESS', 'Принято в работу'),
        ('REJECTED', 'Не принято'),
        ('DISCUSSION', 'К обсуждению'),
        ('DONE', 'Выполнено'),
        ('CLOSED', 'Закрыто'),
        ('CANCELLED', 'Аннулировано'),
    ]

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='issues')
    version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issues',
    )
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_issues')
    assignee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_issues',
    )
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NEW')
    # SPEC 2.0, волна 0, п. 6: блокирующий флаг замечания. Итог A/B/C
    # вычисляется из открытых замечаний и этого флага; смена флага — только
    # через сервис с обязательным обоснованием и записью в IssueFlagLog.
    is_blocking = models.BooleanField(
        default=False,
        verbose_name="Блокирующее замечание",
    )
    external_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        db_index=True,
        verbose_name="Внешний ID",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='closed_issues',
    )
    page_number = models.PositiveIntegerField(null=True, blank=True)
    x_ratio = models.FloatField(null=True, blank=True)
    y_ratio = models.FloatField(null=True, blank=True)
    x1_ratio = models.FloatField(null=True, blank=True)
    y1_ratio = models.FloatField(null=True, blank=True)
    x2_ratio = models.FloatField(null=True, blank=True)
    y2_ratio = models.FloatField(null=True, blank=True)
    crop_image = models.ImageField(
        upload_to='issues/crops/%Y/%m/',
        null=True,
        blank=True,
    )
    department = models.CharField(max_length=255, blank=True)
    project_organization = models.CharField(max_length=255, blank=True)
    due_date = models.DateField(null=True, blank=True)
    actual_fix_date = models.DateField(null=True, blank=True)
    revision = models.CharField(max_length=100, blank=True)
    answer_text = models.TextField(blank=True)

    # Этап C (ISSUES_TAB_PLAN, раздел 3.4): M2M-связи блокировки.
    blocked_design_tasks = models.ManyToManyField(
        'storage.DesignTask', blank=True, related_name='blocking_issues',
        verbose_name="Блокирует задания",
    )
    blocked_rfi_tasks = models.ManyToManyField(
        'storage.RfiTask', blank=True, related_name='blocking_issues',
        verbose_name="Блокирует RFI",
    )

    class Meta:
        ordering = ['-created_at']
        constraints = [
            CheckConstraint(
                condition=Q(
                    Q(page_number__isnull=True, x_ratio__isnull=True, y_ratio__isnull=True)
                    | Q(page_number__isnull=False, x_ratio__isnull=False, y_ratio__isnull=False)
                ),
                name='issue_pin_coords_consistency',
            )
        ]

    def __str__(self):
        return f'Замечание #{self.pk} — {self.document.code} ({self.get_status_display()})'


class IssueFlagLog(models.Model):
    """Протокол смены блокирующего флага (SPEC 2.0, волна 0, п. 6).

    Каждая смена флага — с обязательным обоснованием; запись создаётся
    атомарно с переключением в ``issues.services.set_issue_blocking``.
    """

    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name='flag_logs',
        verbose_name='Замечание',
    )
    old_value = models.BooleanField(verbose_name='Было блокирующим')
    new_value = models.BooleanField(verbose_name='Стало блокирующим')
    justification = models.TextField(verbose_name='Обоснование')
    changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issue_flag_changes',
        verbose_name='Кто изменил',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Дата изменения',
    )

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name = 'Запись журнала флага замечания'
        verbose_name_plural = 'Журнал флагов замечаний'

    def __str__(self):
        return 'Флаг замечания #{}: {} → {}'.format(
            self.issue_id, self.old_value, self.new_value,
        )


class IssueComment(models.Model):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name='comments',
        verbose_name='Замечание',
    )
    author = models.ForeignKey(
        'auth.User',
        on_delete=models.CASCADE,
        verbose_name='Автор',
    )
    text = models.TextField(verbose_name='Текст комментария')
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Дата создания',
    )

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Комментарий'
        verbose_name_plural = 'Комментарии'

    def __str__(self):
        return f'Комментарий от {self.author.username} к #{self.issue.pk}'


class Notification(models.Model):
    """Внутреннее уведомление (Волна 3): колокольчик в топбаре.

    Создаётся в сервисных функциях при смене статуса/назначении/комментарии
    замечания и при workflow-переходах зон ISO 19650. Личный инбокс —
    экран «Мои замечания» /issues/my-issues/.
    """

    VERB_CHOICES = [
        ('assigned', 'Назначено замечание'),
        ('commented', 'Новый комментарий'),
        ('status_changed', 'Изменён статус замечания'),
        ('doc_submitted', 'Документ подан на согласование'),
        ('doc_published', 'Документ опубликован'),
        ('doc_returned', 'Документ возвращён в работу'),
        ('doc_archived', 'Документ отправлен в архив'),
        ('discussion_opened', 'Замечание переведено в обсуждение'),
        ('blocking_changed', 'Изменён блокирующий флаг замечания'),
    ]

    recipient = models.ForeignKey(
        'auth.User',
        on_delete=models.CASCADE,
        related_name='notifications',
        verbose_name='Получатель',
    )
    actor = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Инициатор',
    )
    issue = models.ForeignKey(
        Issue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='Замечание',
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='Документ',
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='Проект',
    )
    verb = models.CharField(max_length=30, choices=VERB_CHOICES)
    message = models.CharField(max_length=300, blank=True, default='')
    url = models.CharField(max_length=300, blank=True, default='')
    is_read = models.BooleanField(default=False, verbose_name='Прочитано')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
        ]
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'

    def __str__(self):
        return f'{self.get_verb_display()} → {self.recipient.username}'
