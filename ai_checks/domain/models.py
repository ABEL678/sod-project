"""Модели ИИ-контура (Этап 19, ARCHITECTURE.md 3.2).

Только добавление: существующие модели storage/issues не изменяются.
Ф0.1: LLMCallLog и CheckRun (CheckRun — обязательная FK-цель для
LLMCallLog.check_run). Ф0.2: InternalDocument и InternalChunk
(источники знаний, tenant_scope по 8.2). Остальные модели — в Ф1.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from pgvector.django import VectorField


class InternalDocument(models.Model):
    """Источник знаний ИИ-контура (ARCHITECTURE.md 3.2, 8.2).

    tenant_scope задаёт видимость (8.2): global — всем, company — одной
    компании, project — одному проекту (company наследуется от проекта).
    """

    class TenantScope(models.TextChoices):
        GLOBAL = 'global', 'Глобальный'
        COMPANY = 'company', 'Компания'
        PROJECT = 'project', 'Проект'

    class DocType(models.TextChoices):
        CUSTOMER_REQ = 'customer_req', 'Требования заказчика'
        PAST_ISSUES = 'past_issues', 'Прошлые замечания'
        INTERNAL_STANDARD = 'internal_standard', 'Внутренний стандарт'
        OTHER = 'other', 'Другое'

    tenant_scope = models.CharField(
        max_length=10,
        choices=TenantScope.choices,
        default=TenantScope.COMPANY,
        verbose_name='Уровень видимости',
    )
    company = models.ForeignKey(
        'storage.Company',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='internal_docs',
        verbose_name='Компания',
    )
    project = models.ForeignKey(
        'storage.Project',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='internal_docs',
        verbose_name='Проект',
    )
    source_document = models.ForeignKey(
        'storage.Document',
        on_delete=models.CASCADE,
        related_name='internal_docs',
        verbose_name='Исходный документ',
    )
    doc_type = models.CharField(
        max_length=30,
        choices=DocType.choices,
        verbose_name='Тип источника',
    )
    title = models.CharField(max_length=255, verbose_name='Название')
    is_active = models.BooleanField(
        default=True, verbose_name='Включать в индекс',
    )
    embedding_model = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Модель эмбеддингов',
    )
    indexed_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Проиндексирован',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='internal_docs',
        verbose_name='Создал',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name='Создан',
    )
    updated_at = models.DateTimeField(
        auto_now=True, verbose_name='Обновлён',
    )

    class Meta:
        verbose_name = 'Внутренний документ'
        verbose_name_plural = 'Внутренние документы'
        ordering = ['-created_at']
        constraints = [
            # ARCHITECTURE.md 8.2: обязательные поля по tenant_scope.
            models.CheckConstraint(
                condition=(
                    models.Q(tenant_scope='global')
                    & models.Q(company__isnull=True)
                    & models.Q(project__isnull=True)
                ) | (
                    models.Q(tenant_scope='company')
                    & models.Q(company__isnull=False)
                ) | (
                    models.Q(tenant_scope='project')
                    & models.Q(project__isnull=False)
                ),
                name='ai_internal_doc_tenant_scope',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.title} ({self.get_tenant_scope_display()})'


class InternalChunk(models.Model):
    """Фрагмент источника знаний с вектором bge-m3 (ARCHITECTURE.md 3.2)."""

    document = models.ForeignKey(
        InternalDocument,
        on_delete=models.CASCADE,
        related_name='chunks',
        verbose_name='Внутренний документ',
    )
    seq = models.PositiveIntegerField(verbose_name='Порядок фрагмента')
    text = models.TextField(verbose_name='Текст фрагмента')
    page_from = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Страница с',
    )
    page_to = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Страница по',
    )
    embedding = VectorField(
        dimensions=1024, verbose_name='Вектор (bge-m3 dense)',
    )

    class Meta:
        verbose_name = 'Фрагмент внутреннего документа'
        verbose_name_plural = 'Фрагменты внутренних документов'
        ordering = ['document', 'seq']
        constraints = [
            models.UniqueConstraint(
                fields=['document', 'seq'],
                name='ai_internal_chunk_doc_seq',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.document_id} #{self.seq}'


class CheckRun(models.Model):
    """Прогон проверки версии документа (ARCHITECTURE.md 3.2, Ф1).

    В Ф0.1 модель создаётся как инфраструктурная зависимость LLMCallLog;
    движок правил появится в Ф1. Мультитенантность наследуется от
    document_version → document → project (собственных tenant-полей нет).
    """

    class Status(models.TextChoices):
        QUEUED = 'queued', 'В очереди'
        RUNNING = 'running', 'Выполняется'
        DONE = 'done', 'Завершён'
        FAILED = 'failed', 'Ошибка'
        CANCELED = 'canceled', 'Отменён'

    document_version = models.ForeignKey(
        'storage.DocumentVersion',
        on_delete=models.PROTECT,
        related_name='check_runs',
        verbose_name='Версия документа',
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='check_runs',
        verbose_name='Инициатор',
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.QUEUED,
        verbose_name='Статус',
    )
    engine_version = models.CharField(
        max_length=50,
        blank=True,
        verbose_name='Версия движка (правила/промпты)',
    )
    rules_total = models.PositiveIntegerField(
        default=0, verbose_name='Всего правил',
    )
    findings_total = models.PositiveIntegerField(
        default=0, verbose_name='Всего находок',
    )
    meta = models.JSONField(
        default=dict, blank=True, verbose_name='Параметры прогона',
    )
    started_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Начат',
    )
    finished_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Завершён',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name='Создан',
    )

    class Meta:
        verbose_name = 'Прогон проверки'
        verbose_name_plural = 'Прогоны проверок'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'CheckRun #{self.pk} ({self.get_status_display()})'


class LLMCallLog(models.Model):
    """Журнал вызовов LLM (ARCHITECTURE.md 3.2, Этап 19).

    Правило приватности: тексты промптов и ответов в БД не хранятся —
    только метаданные вызова; детальные логи — в лог-файле приложения.
    """

    class Purpose(models.TextChoices):
        CHECK_EXPLAIN = 'check_explain', 'Объяснение находки'
        RAG_ANSWER = 'rag_answer', 'Ответ RAG'
        GIP_REPORT = 'gip_report', 'Отчёт ГИПа'

    class Status(models.TextChoices):
        OK = 'ok', 'Успешно'
        ERROR = 'error', 'Ошибка'

    provider = models.CharField(
        max_length=50, verbose_name='Провайдер',
    )
    model = models.CharField(
        max_length=100, verbose_name='Модель',
    )
    purpose = models.CharField(
        max_length=30,
        choices=Purpose.choices,
        verbose_name='Назначение',
    )
    prompt_tokens = models.PositiveIntegerField(
        default=0, verbose_name='Токенов промпта',
    )
    completion_tokens = models.PositiveIntegerField(
        default=0, verbose_name='Токенов ответа',
    )
    cost_est = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name='Оценка стоимости, USD',
    )
    latency_ms = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Задержка, мс',
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OK,
        verbose_name='Статус',
    )
    error_message = models.TextField(
        blank=True, verbose_name='Текст ошибки',
    )
    prompt_version = models.CharField(
        max_length=50, blank=True, verbose_name='Версия промпта',
    )
    check_run = models.ForeignKey(
        CheckRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='llm_calls',
        verbose_name='Прогон',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='llm_calls',
        verbose_name='Инициатор',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name='Создано',
    )

    class Meta:
        verbose_name = 'Вызов LLM'
        verbose_name_plural = 'Журнал вызовов LLM'
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['purpose', 'created_at'],
                name='ai_llm_purpose_created_idx',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.provider}/{self.model} ({self.get_purpose_display()})'
