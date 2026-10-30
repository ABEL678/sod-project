import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html

from issues.models import Issue


STATUS_BADGES = {
    'NEW': 'bg-danger',
    'IN_PROGRESS': 'bg-warning text-dark',
    'DISCUSSION': 'bg-info text-dark',
    'REJECTED': 'bg-secondary',
    'DONE': 'bg-primary',
    'CLOSED': 'bg-success',
    'CANCELLED': 'bg-dark',
}


def short_author(user):
    full_name = (user.get_full_name() or '').strip()
    if not full_name:
        return user.username
    parts = full_name.split()
    if len(parts) == 1:
        return parts[0]
    initials = ''.join(part[0] + '.' for part in parts[1:] if part)
    return f'{parts[0]} {initials}'


class IssueTable(tables.Table):
    def __init__(self, *args, is_public=False, **kwargs):
        super().__init__(*args, **kwargs)
        if is_public:
            self.row_attrs = {}

    id = tables.Column(accessor='pk', verbose_name='ID', orderable=False)
    description = tables.TemplateColumn(
        '<span title="{{ record.description }}" '
        'style="display:inline-block; max-width:250px; text-overflow:ellipsis; '
        'overflow:hidden; white-space:nowrap; vertical-align:middle;">'
        '{{ record.description|truncatechars:30 }}</span>',
        verbose_name='Текст',
        orderable=False,
    )
    status = tables.Column(verbose_name='Статус', orderable=False)
    created_at = tables.DateColumn(
        format='d.m.Y',
        verbose_name='Дата',
        orderable=False,
    )
    author = tables.Column(verbose_name='Автор', orderable=False)

    class Meta:
        model = Issue
        fields = ('id', 'description', 'status', 'created_at', 'author')
        empty_text = 'Замечаний пока нет'
        attrs = {
            'class': 'table table-sm table-hover align-middle mb-0',
        }
        row_attrs = {
            'hx-get': lambda record: reverse(
                'issues:issue_detail_htmx', args=[record.pk]
            ),
            'hx-target': '#issue-card-content',
            'hx-swap': 'innerHTML',
            'hx-indicator': '#issue-detail-spinner',
            'hx-on::after-request': 'showIssueCard()',
            'style': 'cursor: pointer;',
        }

    def render_status(self, value, record):
        return format_html(
            '<span class="badge {}">{}</span>',
            STATUS_BADGES.get(value, 'bg-secondary'),
            record.get_status_display(),
        )

    def render_author(self, value, record):
        return short_author(value)
