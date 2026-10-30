from django.contrib.auth.models import User
from django.db import models
from django.db.models import CheckConstraint, Q

from storage.domain.models import Document, DocumentVersion


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
