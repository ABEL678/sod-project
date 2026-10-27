from django.contrib.auth.models import User
from django.db import models

from storage.domain.models import Document, DocumentVersion


class Issue(models.Model):
    STATUS_CHOICES = [
        ('open', 'Открыто'),
        ('in_progress', 'В работе'),
        ('closed', 'Закрыто'),
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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Замечание #{self.pk} — {self.document.code} ({self.get_status_display()})'
