from django.urls import reverse
from django.utils.html import format_html
import django_tables2 as tables

from storage.domain.models import Document


STATUS_BADGE_CLASSES = {
    'approved': 'doc-badge-approved',
    'in_review': 'doc-badge-review',
    'archived': 'doc-badge-archived',
    'draft': 'doc-badge-draft',
}


class DocumentTable(tables.Table):
    """Таблица документов проекта для django-tables2 / django-scotty."""

    selection = tables.TemplateColumn(
        verbose_name='',
        orderable=False,
        exclude_from_export=True,
        template_code=(
            '<input type="checkbox" class="doc-select" name="document_ids" '
            'value="{{ record.pk }}" aria-label="Выбрать документ">'
        ),
        attrs={
            'th': {'class': 'doc-select-col'},
            'td': {'class': 'doc-select-col'},
        },
    )
    code = tables.Column(verbose_name='Шифр')
    name = tables.Column(verbose_name='Название')
    doc_type = tables.Column(verbose_name='Тип')
    status = tables.Column(verbose_name='Статус')
    current_version = tables.Column(
        verbose_name='Версия',
        order_by=('current_version__version_number',),
    )
    updated_at = tables.Column(verbose_name='Дата')
    created_by = tables.Column(verbose_name='Автор')

    class Meta:
        model = Document
        attrs = {
            'class': 'table table-hover table-sm doc-table',
            'id': 'documentsTable',
            'data-role': 'documents-table',
        }
        sequence = (
            'selection',
            'code',
            'name',
            'doc_type',
            'status',
            'current_version',
            'updated_at',
            'created_by',
        )
        order_by = '-updated_at'

    def __init__(self, *args, **kwargs):
        # CottonTableView передаёт свои служебные аргументы в table_class.
        kwargs.pop('available_actions', None)
        kwargs.pop('post_paginate_hook', None)
        super().__init__(*args, **kwargs)

    def render_name(self, record):
        share_url = reverse('storage:document_share_link', args=[record.pk])
        return format_html(
            '<span class="doc-name-cell">'
            '<a href="{}">{}</a>'
            '<button type="button" class="doc-share-btn" data-share-url="{}" '
            'title="Скопировать ссылку" aria-label="Скопировать ссылку">'
            '<i class="bi bi-link-45deg" aria-hidden="true"></i>'
            '</button>'
            '</span>',
            reverse('storage:document_detail', args=[record.pk]),
            record.name,
            share_url,
        )

    def render_doc_type(self, record):
        return record.get_doc_type_display()

    def render_status(self, record):
        return format_html(
            '<span class="doc-badge {}">{}</span>',
            STATUS_BADGE_CLASSES.get(record.status, 'doc-badge-draft'),
            record.get_status_display(),
        )

    def render_current_version(self, record):
        if record.current_version is None:
            return '—'
        return record.current_version.version_number

    def render_updated_at(self, record):
        return record.updated_at.strftime('%d.%m.%Y %H:%M')

    def render_created_by(self, record):
        return record.created_by.get_full_name() or record.created_by.username
