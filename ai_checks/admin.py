"""Админка ИИ-контура (Этап 19).

Доступна только при AI_CHECKS_ENABLED=True (ARCHITECTURE.md 8.1):
флаг включает UI контура, миграции применяются независимо.
"""

from django.conf import settings
from django.contrib import admin, messages

from ai_checks.domain.models import InternalChunk, InternalDocument
from ai_checks.domain.services import index_document


if settings.AI_CHECKS_ENABLED:

    @admin.register(InternalDocument)
    class InternalDocumentAdmin(admin.ModelAdmin):
        list_display = (
            'title', 'tenant_scope', 'doc_type', 'source_document',
            'is_active', 'indexed_at',
        )
        list_filter = ('tenant_scope', 'doc_type', 'is_active')
        search_fields = ('title', 'source_document__code', 'source_document__name')
        autocomplete_fields = ()
        readonly_fields = ('embedding_model', 'indexed_at', 'created_at', 'updated_at')
        actions = ['index_selected']

        @admin.action(description='Проиндексировать выбранные')
        def index_selected(self, request, queryset):
            indexed = 0
            for internal_doc in queryset:
                version = internal_doc.source_document.current_version
                if version is None or not version.file:
                    self.message_user(
                        request,
                        f'«{internal_doc.title}»: у исходного документа нет '
                        'текущей версии с файлом.',
                        level=messages.ERROR,
                    )
                    continue
                try:
                    count = index_document(internal_doc, version.file.path)
                except (OSError, RuntimeError, ValueError) as exc:
                    # RuntimeError — битый PDF (pymupdf.FileDataError).
                    self.message_user(
                        request,
                        f'«{internal_doc.title}»: ошибка индексации — {exc}',
                        level=messages.ERROR,
                    )
                    continue
                indexed += 1
                self.message_user(
                    request,
                    f'«{internal_doc.title}»: создано фрагментов — {count}.',
                    level=messages.SUCCESS,
                )
            if not indexed and queryset.exists():
                self.message_user(
                    request, 'Ни один документ не проиндексирован.',
                    level=messages.WARNING,
                )

    @admin.register(InternalChunk)
    class InternalChunkAdmin(admin.ModelAdmin):
        list_display = ('document', 'seq', 'page_from', 'page_to')
        list_filter = ('document',)
        readonly_fields = ('document', 'seq', 'text', 'page_from', 'page_to')
