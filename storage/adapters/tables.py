from django.urls import reverse
from django.utils.html import format_html
import django_tables2 as tables
from django_tables2.utils import AttributeDict

from storage.domain.models import Document


STATUS_BADGE_CLASSES = {
    'approved': 'doc-badge-approved',
    'in_review': 'doc-badge-review',
    'archived': 'doc-badge-archived',
    'draft': 'doc-badge-draft',
}


def _column_attrs(key, td_class=''):
    td = {'data-column': key}
    if td_class:
        td['class'] = td_class
    return {
        'th': {'data-column': key, 'scope': 'col'},
        'td': td,
    }


class DocumentTable(tables.Table):
    """Таблица документов проекта на стандартном шаблоне Bootstrap 5."""

    selection = tables.CheckBoxColumn(
        accessor='pk',
        verbose_name='',
        orderable=False,
        exclude_from_export=True,
        attrs={
            'th': {'class': 'doc-select-col', 'data-column': 'selection'},
            'td': {'class': 'doc-select-col', 'data-column': 'selection'},
            'input': {
                'class': 'form-check-input doc-select',
            },
            'th__input': {
                'id': 'selectAllDocuments',
                'class': 'form-check-input',
                'aria-label': 'Выбрать все документы',
            },
            'td__input': {
                'name': 'document_ids',
                'class': 'form-check-input doc-select',
            },
        },
    )
    code = tables.Column(verbose_name='Шифр', attrs=_column_attrs('code'))
    name = tables.Column(verbose_name='Название', attrs=_column_attrs('name'))
    doc_type = tables.Column(verbose_name='Тип', attrs=_column_attrs('doc_type'))
    status = tables.Column(verbose_name='Статус', attrs=_column_attrs('status'))
    current_version = tables.Column(
        verbose_name='Версия',
        order_by=('current_version__version_number',),
        attrs=_column_attrs('version'),
    )
    updated_at = tables.Column(verbose_name='Дата', attrs=_column_attrs('date'))
    created_by = tables.Column(verbose_name='Автор', attrs=_column_attrs('author'))

    class Meta:
        model = Document
        attrs = {
            'class': 'table table-hover table-bordered align-middle mb-0 doc-table',
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
        template_name = 'storage/tables/bootstrap5.html'

    def __init__(self, *args, **kwargs):
        # CottonTableView передаёт свои служебные аргументы в table_class.
        kwargs.pop('available_actions', None)
        kwargs.pop('post_paginate_hook', None)
        self.highlight_id = None
        super().__init__(*args, **kwargs)
        self.row_attrs = AttributeDict({
            'data-row': 'document',
            'data-document-id': lambda record: str(record.pk),
            'data-document-url': lambda record: reverse(
                'storage:document_detail', args=[record.pk]
            ),
            'data-share-url': lambda record: reverse(
                'storage:document_share_link', args=[record.pk]
            ),
            'data-delete-url': lambda record: reverse(
                'storage:document_delete', args=[record.pk]
            ),
            'data-document-code': lambda record: record.code,
            'draggable': 'true',
            'data-search': lambda record: ' '.join([
                record.code.lower(),
                record.name.lower(),
                record.get_doc_type_display().lower(),
                record.get_status_display().lower(),
                (record.created_by.get_full_name() or record.created_by.username).lower(),
            ]),
            'class': lambda record: (
                'doc-row-new' if record.pk == self.highlight_id else ''
            ),
        })

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
