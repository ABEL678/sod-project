import django_tables2 as tables
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


class IssueTable(tables.Table):
    def __init__(self, *args, is_public=False, **kwargs):
        super().__init__(*args, **kwargs)
        if is_public:
            self.row_attrs = {}
            self.columns['actions'].column.visible = False

    id = tables.Column(verbose_name='ID', orderable=False)
    description = tables.Column(
        verbose_name='Текст',
        orderable=False,
        attrs={'td': {'class': 'issue-table-text'}},
    )
    status = tables.Column(verbose_name='Статус', orderable=False)
    created_at = tables.DateColumn(
        format='d.m.Y',
        verbose_name='Дата',
        orderable=False,
    )
    author = tables.Column(
        accessor='author.username',
        verbose_name='Автор',
        orderable=False,
    )
    actions = tables.TemplateColumn(
        """<button class="btn btn-sm btn-outline-primary"
            hx-get="{% url 'issues:issue_detail_htmx' record.pk %}"
            hx-target="#issue-card-content"
            hx-swap="innerHTML"
            hx-indicator="#issue-detail-spinner"
            onclick="showIssueCard()">Детали</button>""",
        verbose_name='',
        orderable=False,
    )

    class Meta:
        model = Issue
        fields = (
            'id',
            'description',
            'status',
            'created_at',
            'author',
            'actions',
        )
        empty_text = 'Замечаний пока нет'
        attrs = {
            'class': 'table table-sm small table-hover align-middle mb-0',
        }
        row_attrs = {
            'id': lambda record: f'issue-row-{record.pk}',
            'data-issue-id': lambda record: record.pk,
            'style': 'cursor: pointer;',
            'onclick': 'selectIssue(event, this.dataset.issueId)',
        }

    def render_status(self, value, record):
        return format_html(
            '<span class="badge {}">{}</span>',
            STATUS_BADGES.get(value, 'bg-secondary'),
            record.get_status_display(),
        )
