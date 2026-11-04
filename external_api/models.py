from django.conf import settings
from django.db import models


class ApiToken(models.Model):
    """API-ключ для внешних систем (ГИП-Ассистент, ВОР-билдер, Витро-трекер)."""

    class Scope(models.TextChoices):
        PROJECTS_CREATE = "projects:create", "Создание проектов"
        PROJECTS_READ = "projects:read", "Чтение проектов"
        ISSUES_SYNC = "issues:sync", "Синхронизация замечаний"
        ISSUES_READ = "issues:read", "Чтение замечаний"

    company = models.ForeignKey(
        "storage.Company",
        on_delete=models.CASCADE,
        related_name="api_tokens",
        verbose_name="Компания",
    )
    name = models.CharField(max_length=100, verbose_name="Название (для кого)")
    token_prefix = models.CharField(
        max_length=8,
        verbose_name="Префикс токена (первые 8 символов, для поиска)",
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        verbose_name="SHA-256 хэш токена",
    )
    scopes = models.JSONField(default=list, verbose_name="Scopes")
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_api_tokens",
        verbose_name="Создатель",
    )
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "is_active"])]
        verbose_name = "API-токен"
        verbose_name_plural = "API-токены"

    def __str__(self):
        return f"{self.name} ({self.token_prefix}…)"
