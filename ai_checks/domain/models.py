"""Модели ИИ-контура (Этап 19, ARCHITECTURE.md 3.2).

Только добавление: существующие модели storage/issues не изменяются.
Ф0.2: InternalDocument и InternalChunk (источники знаний, tenant_scope
по 8.2). Финальная архитектура ИИ-контура: ProjectDocumentChunk (чанки
PDF-версий документов проекта, треб. 1) и RagQuery (история ИИ-поиска,
треб. 2), GeneratedReport (отчёт ГИПу) и LLMCallLog (журнал вызовов).

Зачистка 2026-11-09: ИИ-нормоконтроль СПДС выведен из эксплуатации —
модели RuleDefinition, CheckRun, PendingCheckResult и ReminderRule
удалены (миграция 0013); остался RAG-поиск, база знаний и отчёт ГИПу.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from pgvector.django import VectorField


class IndexingStatus(models.TextChoices):
    """Единый статус ИИ-индексации (треб. 3: pending/processing/done/error).

    Используется и InternalDocument (база знаний), и DocumentVersion
    (документы проекта) — чтобы UI показывал одинаковые бейджи.
    """

    PENDING = 'pending', 'В очереди'
    PROCESSING = 'processing', 'Индексируется'
    DONE = 'done', 'Готово'
    ERROR = 'error', 'Ошибка'


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
    indexing_status = models.CharField(
        max_length=10,
        choices=IndexingStatus.choices,
        default=IndexingStatus.PENDING,
        verbose_name='Статус индексации',
    )
    index_error = models.TextField(
        blank=True, verbose_name='Ошибка индексации',
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
            # global  ⇒ company/project NULL;
            # company ⇒ company NOT NULL И project NULL;
            # project ⇒ project NOT NULL (company наследуется от проекта).
            models.CheckConstraint(
                condition=(
                    models.Q(tenant_scope='global')
                    & models.Q(company__isnull=True)
                    & models.Q(project__isnull=True)
                ) | (
                    models.Q(tenant_scope='company')
                    & models.Q(company__isnull=False)
                    & models.Q(project__isnull=True)
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


class ProjectDocumentChunk(models.Model):
    """Фрагмент PDF-версии документа проекта (треб. 1).

    Проектный индекс для сквозного RAG (треб. 6): чанки текущих и
    исторических версий документов storage.Document. section/folder —
    снимки принадлежности на момент индексации (для фильтра по разделу
    ОВ/ЭОМ/АР и ссылок в источниках). Вектор — bge-m3 dense, 1024.
    """

    document = models.ForeignKey(
        'storage.Document',
        on_delete=models.CASCADE,
        related_name='ai_chunks',
        verbose_name='Документ проекта',
    )
    version = models.ForeignKey(
        'storage.DocumentVersion',
        on_delete=models.CASCADE,
        related_name='ai_chunks',
        verbose_name='Версия документа',
    )
    section = models.ForeignKey(
        'storage.Section',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='ai_chunks',
        verbose_name='Раздел',
    )
    folder = models.ForeignKey(
        'storage.Folder',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='ai_chunks',
        verbose_name='Папка',
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
    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name='Создан',
    )

    class Meta:
        verbose_name = 'Фрагмент документа проекта'
        verbose_name_plural = 'Фрагменты документов проектов'
        ordering = ['document', 'seq']
        constraints = [
            models.UniqueConstraint(
                fields=['document', 'version', 'seq'],
                name='ai_project_chunk_doc_ver_seq',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.document_id}/v{self.version_id} #{self.seq}'


class RagQuery(models.Model):
    """История ИИ-поиска (треб. 2, 9, 10).

    Одна строка = один запрос пользователя: вопрос, ответ, снимок
    источников (sources JSON) и модель LLM. Видна всем участникам
    проекта (треб. 9); поиск/фильтры/пагинация — по этому индексу
    (треб. 10). Тексты промптов в БД не хранятся (приватность).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='rag_queries',
        verbose_name='Автор',
    )
    project = models.ForeignKey(
        'storage.Project',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='rag_queries',
        verbose_name='Проект',
    )
    question = models.TextField(verbose_name='Вопрос')
    answer = models.TextField(
        blank=True, verbose_name='Ответ',
    )
    sources = models.JSONField(
        default=list, blank=True, verbose_name='Источники',
    )
    model = models.CharField(
        max_length=100, blank=True, verbose_name='Модель LLM',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name='Создан',
    )

    class Meta:
        verbose_name = 'Запрос ИИ-поиска'
        verbose_name_plural = 'История ИИ-поиска'
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['project', '-created_at'],
                name='ai_rag_project_created_idx',
            ),
        ]

    def __str__(self) -> str:
        return f'RagQuery #{self.pk} ({self.question[:40]})'


class GeneratedReport(models.Model):
    """Сгенерированный отчёт для ГИПа (ARCHITECTURE.md, дополнение к 7).

    payload — сводка: total/by_severity/by_document/top_issues + текст
    резюме. period — ISO-диапазон 'YYYY-MM-DD..YYYY-MM-DD'.
    Тексты промптов/ответов не хранятся (приватность), только метаданные.
    """

    project = models.ForeignKey(
        'storage.Project',
        on_delete=models.CASCADE,
        related_name='ai_reports',
        verbose_name='Проект',
    )
    period = models.CharField(
        max_length=64, verbose_name='Период (ISO: start..end)',
    )
    payload = models.JSONField(
        default=dict, verbose_name='Сводка',
    )
    llm_call = models.ForeignKey(
        'LLMCallLog',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reports',
        verbose_name='Вызов LLM',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='ai_reports',
        verbose_name='Создал',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name='Создан',
    )

    class Meta:
        verbose_name = 'Отчёт ГИПу'
        verbose_name_plural = 'Отчёты ГИПу'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'Отчёт #{self.pk} ({self.period})'


class LLMCallLog(models.Model):
    """Журнал вызовов LLM (ARCHITECTURE.md 3.2, Этап 19).

    Правило приватности: тексты промптов и ответов в БД не хранятся —
    только метаданные вызова; детальные логи — в лог-файле приложения.
    """

    class Purpose(models.TextChoices):
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
