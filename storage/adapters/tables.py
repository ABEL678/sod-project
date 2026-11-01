from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
import django_tables2 as tables
from django_tables2.utils import AttributeDict

from storage.domain.models import Document, Folder
from storage.domain.services import (
    _effective_zone,
    document_actions,
)


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
    suitability_code = tables.Column(
        verbose_name='Внешний статус',
        attrs=_column_attrs('suitability'),
        order_by=('suitability_code',),
    )
    internal_status = tables.Column(
        verbose_name='Внутренний статус',
        attrs=_column_attrs('internal-status'),
        order_by=('internal_status',),
    )
    zone = tables.Column(
        verbose_name='Зона',
        attrs=_column_attrs('zone'),
        order_by=('status',),
        # Волна 3, бонус-фикс: accessor 'zone' у Document отсутствует -> None
        # попадал в empty_values и render_zone не вызывался (в колонке всегда
        # был прочерк). Разрешаем пустые значения — '—' рисует сам render_zone.
        empty_values=(),
    )
    current_version = tables.Column(
        verbose_name='Версия',
        order_by=('current_version__version_number',),
        attrs=_column_attrs('version'),
    )
    updated_at = tables.Column(verbose_name='Дата', attrs=_column_attrs('date'))
    created_by = tables.Column(verbose_name='Автор', attrs=_column_attrs('author'))
    actions = tables.Column(
        verbose_name='Действия',
        orderable=False,
        exclude_from_export=True,
        # Пустой аксессуар нельзя пропускать: иначе django_tables2 рендерит
        # «—» вместо render_actions (None в empty_values).
        empty_values=(),
        attrs={
            'th': {'data-column': 'actions', 'scope': 'col', 'class': 'doc-actions-col'},
            'td': {'data-column': 'actions', 'class': 'doc-actions-col'},
        },
    )

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
            'suitability_code',
            'internal_status',
            'zone',
            'current_version',
            'updated_at',
            'created_by',
            'actions',
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

    def render_suitability_code(self, record):
        return format_html(
            '<span class="doc-suitability" title="{}">{}</span>',
            record.get_suitability_code_display(),
            record.get_suitability_code_display(),
        )

    def render_internal_status(self, record):
        if _effective_zone(record) != 'WIP':
            return '—'
        return format_html(
            '<span class="doc-internal-status" title="{}">{}</span>',
            record.get_internal_status_display(),
            record.get_internal_status_display(),
        )

    def render_zone(self, record):
        zone_code = _effective_zone(record)
        if not zone_code:
            return '—'
        zone_icons = {
            'WIP': 'bi-pencil-square',
            'SHARED': 'bi-people',
            'PUBLISHED': 'bi-shield-check',
            'ARCHIVE': 'bi-lock',
        }
        icon = zone_icons.get(zone_code, 'bi-folder')
        zone_name = dict(Folder.ZONE_CHOICES).get(zone_code, zone_code)
        zone_class = {
            'WIP': 'text-primary',
            'SHARED': 'text-warning',
            'PUBLISHED': 'text-success',
            'ARCHIVE': 'text-secondary',
        }.get(zone_code, '')
        return format_html(
            '<i class="bi {} {} me-1" title="{}" aria-hidden="true"></i>{}',
            icon, zone_class, zone_name, zone_name,
        )

    def render_current_version(self, record):
        if record.current_version is None:
            return '—'
        return record.current_version.version_number

    def render_updated_at(self, record):
        return record.updated_at.strftime('%d.%m.%Y %H:%M')

    def render_created_by(self, record):
        return record.created_by.get_full_name() or record.created_by.username

    def render_actions(self, record):
        """Кнопки внутреннего workflow и зон ISO 19650."""
        user = getattr(self.request, 'user', None)
        actions = document_actions(user, record)
        if not actions:
            return mark_safe('<span class="text-muted">—</span>')
        buttons = []
        for action in actions:
            if action.get('kind') == 'internal':
                url = reverse(
                    'storage:document_internal_status_update',
                    args=[record.pk],
                )
            else:
                url = reverse('storage:document_workflow', args=[record.pk])
            buttons.append(format_html(
                '<button type="button" class="wf-btn" data-workflow-action="{code}" '
                'data-workflow-url="{url}" title="{title}" '
                'aria-label="{title}">'
                '<i class="bi {icon}" aria-hidden="true"></i>'
                '</button>',
                code=action['code'],
                url=url,
                title=action['label'],
                icon=action['icon'],
            ))
        return mark_safe(
            '<span class="wf-actions" data-role="workflow-actions">{}</span>'.format(
                ''.join(str(button) for button in buttons)
            )
        )
