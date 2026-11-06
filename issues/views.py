import json
import re
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import get_valid_filename
from django.views import View
from django.views.generic import CreateView, ListView
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from storage.adapters.views import _issue_payload
from storage.domain.design_colors import (
    EXCEL_HEADER_FILL,
    EXCEL_LINK,
    EXCEL_OVERDUE_TEXT,
)
from storage.domain.services import (
    add_issue_comment,
    allowed_issue_statuses,
    check_issue_permission,
    check_read_permission,
    get_accessible_documents,
    get_accessible_projects,
    get_readable_documents,
    create_issue,
    delete_issue,
    delete_issue_crop,
    update_issue_answer,
    update_issue_status,
)

from issues.models import Issue, Notification


STATUS_CODE_RE = re.compile(
    r'\b(NEW|IN_PROGRESS|REJECTED|DISCUSSION|DONE|CLOSED|CANCELLED)\b'
)


def _humanize_status_codes(text):
    labels = dict(Issue.STATUS_CHOICES)
    return STATUS_CODE_RE.sub(
        lambda match: labels.get(match.group(1), match.group(1)),
        text or '',
    )


def _to_float(value):
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _issue_json(issue, user=None):
    payload = _issue_payload(issue)
    if user is not None:
        labels = dict(Issue.STATUS_CHOICES)
        payload['status_options'] = [
            {'code': code, 'label': labels[code]}
            for code in allowed_issue_statuses(user, issue)
        ]
    return payload


class IssueCreateView(LoginRequiredMixin, CreateView):
    """Создание замечания. Аудит 6.0: явный LoginRequiredMixin —
    аноним раньше доходил до dispatch и получал 404 (fail-closed,
    но без редиректа на вход)."""
    model = Issue
    fields = ['description']
    http_method_names = ['post']

    def dispatch(self, request, *args, **kwargs):
        self.document = get_object_or_404(
            get_accessible_documents(request.user),
            pk=kwargs['document_id'],
        )
        if not check_issue_permission(request.user, self.document):
            return HttpResponseForbidden('Нет прав на добавление замечания')
        return super().dispatch(request, *args, **kwargs)

    def _is_ajax(self):
        return self.request.headers.get('x-requested-with') == 'XMLHttpRequest'

    def _error(self, message, status):
        if self._is_ajax():
            return JsonResponse({'status': 'error', 'error': message}, status=status)
        messages.error(self.request, message)
        return redirect('storage:document_detail', pk=self.document.pk)

    def form_valid(self, form):
        description = form.cleaned_data['description']
        version_id = self.request.POST.get('version_id')
        version = None
        if version_id:
            version = self.document.versions.filter(pk=version_id).first()
        if version is None:
            version = self.document.current_version

        page_number = self.request.POST.get('page_number')
        x_ratio = self.request.POST.get('x_ratio')
        y_ratio = self.request.POST.get('y_ratio')
        x1_ratio = self.request.POST.get('x1_ratio')
        y1_ratio = self.request.POST.get('y1_ratio')
        x2_ratio = self.request.POST.get('x2_ratio')
        y2_ratio = self.request.POST.get('y2_ratio')

        try:
            page_number = int(page_number) if page_number else None
        except (ValueError, TypeError):
            page_number = None
        x_ratio = _to_float(x_ratio)
        y_ratio = _to_float(y_ratio)
        x1_ratio = _to_float(x1_ratio)
        y1_ratio = _to_float(y1_ratio)
        x2_ratio = _to_float(x2_ratio)
        y2_ratio = _to_float(y2_ratio)

        due_date = parse_date(self.request.POST.get('due_date') or '')

        assignee = None
        assignee_id = (self.request.POST.get('assignee') or '').strip()
        if assignee_id:
            assignee = get_user_model().objects.filter(pk=assignee_id).first()
            if assignee is None:
                return self._error('Ответственный не найден', 400)

        try:
            issue = create_issue(
                user=self.request.user,
                document=self.document,
                description=description,
                version=version,
                page_number=page_number,
                x_ratio=x_ratio,
                y_ratio=y_ratio,
                assignee=assignee,
                x1_ratio=x1_ratio,
                y1_ratio=y1_ratio,
                x2_ratio=x2_ratio,
                y2_ratio=y2_ratio,
                crop_image_base64=self.request.POST.get('crop_image_base64'),
                department=self.request.POST.get('department', ''),
                project_organization=self.request.POST.get(
                    'project_organization', ''
                ),
                answer_text=self.request.POST.get('answer_text', ''),
                due_date=due_date,
                revision=self.request.POST.get('revision', ''),
            )
        except PermissionDenied as exc:
            if self._is_ajax():
                return JsonResponse(
                    {'status': 'error', 'error': str(exc)}, status=403
                )
            return HttpResponseForbidden(str(exc))
        except ValueError as exc:
            return self._error(str(exc), 400)

        if self._is_ajax():
            return JsonResponse(
                {'status': 'ok', 'issue': _issue_json(issue, self.request.user)},
                status=201,
            )

        messages.success(self.request, 'Замечание добавлено.')
        return redirect('storage:document_detail', pk=self.document.pk)

    def form_invalid(self, form):
        if self._is_ajax():
            return JsonResponse(
                {'status': 'error', 'error': 'Введите описание замечания.'},
                status=400,
            )
        messages.error(self.request, 'Введите описание замечания.')
        return redirect('storage:document_detail', pk=self.document.pk)


class IssueUpdateStatusView(View):
    def post(self, request, issue_id):
        if not request.user.is_authenticated:
            return HttpResponseForbidden('Нет прав на изменение замечания')

        issues = Issue.objects.select_related(
            'document__project', 'author', 'assignee'
        ).filter(document__in=get_accessible_documents(request.user))

        issue = get_object_or_404(issues, pk=issue_id)

        if not check_read_permission(request.user, issue.document):
            return HttpResponseForbidden('Нет прав на изменение замечания')

        if request.content_type == 'application/json':
            try:
                payload = json.loads(request.body or b'{}')
            except (TypeError, ValueError):
                return JsonResponse(
                    {'error': 'Некорректный JSON'}, status=400
                )
            status = (payload.get('status') or '').strip()
            try:
                update_issue_status(request.user, issue, status)
            except PermissionDenied as exc:
                return JsonResponse({'error': str(exc)}, status=403)
            except ValueError as exc:
                return JsonResponse({'error': str(exc)}, status=400)
            return JsonResponse({'status': 'ok'})

        status = (request.POST.get('status') or '').strip()
        if not status:
            action = request.POST.get('action')
            status = {'open': 'NEW', 'close': 'CLOSED'}.get(action, '')

        try:
            update_issue_status(request.user, issue, status)
        except PermissionDenied as exc:
            return HttpResponseForbidden(str(exc))
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('storage:document_detail', pk=issue.document_id)

        messages.success(request, 'Статус замечания обновлён.')
        return redirect('storage:document_detail', pk=issue.document_id)


class IssueAnswerUpdateView(View):
    def post(self, request, issue_id):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Требуется авторизация'}, status=403)

        issues = Issue.objects.filter(
            document__in=get_accessible_documents(request.user)
        ).select_related('document')
        issue = get_object_or_404(issues, pk=issue_id)
        answer_text = request.POST.get('answer_text', '').strip()
        try:
            update_issue_answer(request.user, issue, answer_text)
        except PermissionDenied as exc:
            return JsonResponse({'error': str(exc)}, status=403)

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse(
                {'status': 'ok', 'answer_text': issue.answer_text}
            )
        messages.success(request, 'Ответ сохранён.')
        return redirect('storage:document_detail', pk=issue.document_id)


class IssueDeleteView(View):
    def post(self, request, issue_id):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Требуется авторизация'}, status=403)

        issues = Issue.objects.filter(
            document__in=get_accessible_documents(request.user)
        )
        issue = get_object_or_404(issues, pk=issue_id)
        try:
            delete_issue(request.user, issue)
        except PermissionDenied as exc:
            return JsonResponse({'error': str(exc)}, status=403)

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'status': 'ok'})
        messages.success(request, 'Замечание удалено.')
        return redirect('storage:document_detail', pk=issue.document_id)


@login_required
def issue_detail_htmx(request, issue_id):
    issues = Issue.objects.select_related(
        'document',
        'document__project',
        'author',
        'assignee',
        'version',
    ).filter(document__in=get_accessible_documents(request.user))
    issue = get_object_or_404(issues, pk=issue_id)
    if not check_read_permission(request.user, issue.document):
        return HttpResponseForbidden('Нет прав на просмотр замечания')

    allowed_statuses = allowed_issue_statuses(request.user, issue)
    labels = dict(Issue.STATUS_CHOICES)
    allowed_status_options = [
        (code, labels[code]) for code in allowed_statuses
    ]
    comments = list(issue.comments.all())
    for comment in comments:
        comment.text_display = _humanize_status_codes(comment.text)

    return render(
        request,
        'issues/_card.html',
        {
            'issue': issue,
            'allowed_statuses': allowed_statuses,
            'allowed_status_options': allowed_status_options,
            'comments': comments,
            'answer_text_display': _humanize_status_codes(
                issue.answer_text
            ),
        },
    )


@login_required
def issue_delete_crop(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    if issue.document not in get_accessible_documents(request.user):
        return HttpResponseForbidden('Нет доступа')

    try:
        delete_issue_crop(request.user, issue)
    except PermissionDenied as exc:
        return HttpResponseForbidden(str(exc))
    return redirect(request.META.get('HTTP_REFERER', 'storage:project_list'))


@login_required
def add_comment(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    if issue.document not in get_accessible_documents(request.user):
        return HttpResponseForbidden('Нет доступа к этому замечанию')

    if request.method == 'POST':
        try:
            add_issue_comment(
                request.user,
                issue,
                request.POST.get('text', ''),
            )
        except PermissionDenied as exc:
            return HttpResponseForbidden(str(exc))
    return redirect(
        request.META.get('HTTP_REFERER', 'storage:project_list')
    )


# ============================================================================
# Реестр замечаний проекта
# ============================================================================

# Группы статусов — те же, что в табах панели замечаний вьювера,
# чтобы «Открытые» означало одно и то же на обоих экранах.
ISSUE_STATUS_GROUPS = {
    'open': ('NEW', 'REJECTED', 'DISCUSSION'),
    'progress': ('IN_PROGRESS',),
    'review': ('DONE',),
    'closed': ('CLOSED', 'CANCELLED'),
}
ACTIVE_ISSUE_STATUSES = ISSUE_STATUS_GROUPS['open'] + ISSUE_STATUS_GROUPS['progress']


def _safe_excel_value(value):
    """Защита от Formula Injection: =, +, -, @ в начале экранируются."""
    if value is None:
        return ''
    if not isinstance(value, str):
        return value
    if value[:1] in ('=', '+', '-', '@'):
        return "'" + value
    return value


def _readable_document_pks(user, project):
    return list(
        get_readable_documents(user, project).values_list('pk', flat=True)
    )


def _registry_issues(user, project):
    """Все замечания проекта, видимые пользователю (по правам на документы)."""
    pks = _readable_document_pks(user, project)
    return Issue.objects.filter(document_id__in=pks).select_related(
        'document',
        'document__project',
        'document__section',
        'author',
        'assignee',
    )


def _apply_registry_filters(queryset, params):
    """Точечные GET-фильтры реестра (те же параметры принимает Excel-экспорт)."""
    status = (params.get('status') or '').strip()
    if status in dict(Issue.STATUS_CHOICES):
        queryset = queryset.filter(status=status)

    flag = (params.get('flag') or '').strip()
    if flag == 'active':
        queryset = queryset.filter(status__in=ACTIVE_ISSUE_STATUSES)
    elif flag == 'open':
        queryset = queryset.exclude(status__in=ISSUE_STATUS_GROUPS['closed'])
    elif flag == 'closed':
        queryset = queryset.filter(status__in=ISSUE_STATUS_GROUPS['closed'])
    elif flag == 'overdue':
        queryset = queryset.filter(
            due_date__isnull=False,
            due_date__lt=timezone.localdate(),
            status__in=ACTIVE_ISSUE_STATUSES,
        )

    assignee = (params.get('assignee') or '').strip()
    if assignee == 'none':
        queryset = queryset.filter(assignee__isnull=True)
    elif assignee.isdigit():
        queryset = queryset.filter(assignee_id=int(assignee))

    query = (params.get('q') or '').strip()
    if query:
        queryset = queryset.filter(
            Q(description__icontains=query)
            | Q(document__code__icontains=query)
            | Q(document__name__icontains=query)
        )
    return queryset


def _registry_context_counts(user, project):
    """Счётчики по группам статусов + просрочка (по полному, нефильнутому набору)."""
    counts = dict(
        _registry_issues(user, project)
        .values_list('status')
        .annotate(total=Count('pk'))
    )
    today = timezone.localdate()
    overdue = _registry_issues(user, project).filter(
        due_date__isnull=False,
        due_date__lt=today,
        status__in=ACTIVE_ISSUE_STATUSES,
    ).count()
    groups = {
        group: sum(counts.get(status, 0) for status in statuses)
        for group, statuses in ISSUE_STATUS_GROUPS.items()
    }
    return {
        'total': sum(counts.values()),
        'groups': groups,
        'active': groups['open'] + groups['progress'],
        'overdue': overdue,
    }


class IssueRegistryView(LoginRequiredMixin, ListView):
    """Реестр всех замечаний проекта: один экран — вся переписка по качеству.

    Отвечает на вопрос ГИПа «что требует внимания прямо сейчас»:
    счётчики по группам статусов, просрочка, фильтры, Excel-выгрузка.
    """

    template_name = 'issues/registry.html'
    context_object_name = 'issues'
    paginate_by = 50

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=kwargs['project_id'],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return _apply_registry_filters(
            _registry_issues(self.request.user, self.project),
            self.request.GET,
        ).order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        project = self.project

        readable_pks = _readable_document_pks(user, project)
        assignee_choices = list(
            get_user_model()
            .objects.filter(
                assigned_issues__document_id__in=readable_pks,
            )
            .distinct()
            .order_by('last_name', 'first_name')
        )

        context.update({
            'project': project,
            'status_choices': Issue.STATUS_CHOICES,
            'assignee_choices': assignee_choices,
            'counts': _registry_context_counts(user, project),
            'filters': {
                'status': (self.request.GET.get('status') or '').strip(),
                'flag': (self.request.GET.get('flag') or '').strip(),
                'assignee': (self.request.GET.get('assignee') or '').strip(),
                'q': (self.request.GET.get('q') or '').strip(),
            },
            'export_url': self._export_url(),
        })
        return context

    def _export_url(self):
        """Ссылка на Excel-выгрузку с текущими фильтрами."""
        query = self.request.GET.urlencode()
        url = reverse('issues:issue_registry_export', args=[self.project.pk])
        return url + ('?' + query if query else '')


class IssueRegistryExportView(LoginRequiredMixin, View):
    """Excel-выгрузка реестра (с учётом активных фильтров)."""

    def get(self, request, project_id):
        project = get_object_or_404(
            get_accessible_projects(request.user),
            pk=project_id,
        )
        issues = list(
            _apply_registry_filters(
                _registry_issues(request.user, project),
                request.GET,
            ).order_by('document__code', 'pk')
        )

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Замечания'

        headers = [
            '№', 'Документ', 'Раздел', 'Статус', 'Текст замечания',
            'Автор', 'Ответственный', 'Срок ответа', 'Просрочено',
            'Создано', 'Ответ', 'Закрыто',
        ]
        sheet.append(headers)
        header_fill = PatternFill('solid', fgColor=EXCEL_HEADER_FILL)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(vertical='center')
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions

        today = timezone.localdate()
        for row, issue in enumerate(issues, start=2):
            doc = issue.document
            sheet.cell(row=row, column=1, value=issue.pk)

            code_cell = sheet.cell(
                row=row, column=2, value=_safe_excel_value(doc.code)
            )
            code_cell.hyperlink = request.build_absolute_uri(
                reverse('storage:document_detail', args=[doc.pk])
            )
            code_cell.font = Font(color=EXCEL_LINK, underline='single')

            sheet.cell(
                row=row, column=3,
                value=_safe_excel_value(doc.section.code if doc.section else ''),
            )
            sheet.cell(row=row, column=4, value=issue.get_status_display())

            text_cell = sheet.cell(
                row=row, column=5, value=_safe_excel_value(issue.description)
            )
            text_cell.alignment = Alignment(wrap_text=True, vertical='top')

            sheet.cell(
                row=row, column=6,
                value=_safe_excel_value(
                    issue.author.get_full_name() or issue.author.username
                ),
            )
            sheet.cell(
                row=row, column=7,
                value=_safe_excel_value(
                    (
                        issue.assignee.get_full_name() or issue.assignee.username
                    ) if issue.assignee else ''
                ),
            )
            sheet.cell(
                row=row, column=8,
                value=issue.due_date.strftime('%d.%m.%Y') if issue.due_date else '',
            )
            overdue = bool(
                issue.due_date
                and issue.due_date < today
                and issue.status in ACTIVE_ISSUE_STATUSES
            )
            overdue_cell = sheet.cell(row=row, column=9, value='да' if overdue else '')
            if overdue:
                overdue_cell.font = Font(color=EXCEL_OVERDUE_TEXT, bold=True)
            sheet.cell(
                row=row, column=10,
                value=timezone.localtime(issue.created_at).strftime('%d.%m.%Y %H:%M'),
            )
            answer_cell = sheet.cell(
                row=row, column=11, value=_safe_excel_value(issue.answer_text)
            )
            answer_cell.alignment = Alignment(wrap_text=True, vertical='top')
            sheet.cell(
                row=row, column=12,
                value=(
                    timezone.localtime(issue.closed_at).strftime('%d.%m.%Y')
                    if issue.closed_at else ''
                ),
            )

        for column, width in {
            'A': 6, 'B': 18, 'C': 10, 'D': 16, 'E': 52, 'F': 24, 'G': 24,
            'H': 13, 'I': 12, 'J': 17, 'K': 40, 'L': 12,
        }.items():
            sheet.column_dimensions[column].width = width

        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        filename = 'reestr_zamechanij_{code}.xlsx'.format(
            code=get_valid_filename(project.code or project.pk),
        )
        return FileResponse(
            buffer,
            as_attachment=True,
            filename=filename,
            content_type=(
                'application/vnd.openxmlformats-officedocument'
                '.spreadsheetml.sheet'
            ),
        )


# ============================================================================
# Волна 3: «Мои замечания» + уведомления
# ============================================================================

def _my_issues_base(user):
    """Замечания, где пользователь автор или ответственный (по правам)."""
    accessible = get_readable_documents(user)
    return (
        Issue.objects.filter(
            document__in=accessible,
            document__is_deleted=False,
        )
        .filter(Q(author=user) | Q(assignee=user))
        .select_related(
            'document',
            'document__project',
            'document__section',
            'author',
            'assignee',
        )
    )


def _my_issues_counts(user):
    """Дайджест «на вас 5, просрочено 2» — по личному набору замечаний."""
    issues = _my_issues_base(user)
    today = timezone.localdate()
    assigned_open = issues.filter(
        assignee=user, status__in=ACTIVE_ISSUE_STATUSES,
    ).count()
    created_open = issues.filter(
        author=user, status__in=ACTIVE_ISSUE_STATUSES,
    ).count()
    overdue = issues.filter(
        assignee=user,
        due_date__isnull=False,
        due_date__lt=today,
        status__in=ACTIVE_ISSUE_STATUSES,
    ).count()
    due_soon = issues.filter(
        assignee=user,
        due_date__isnull=False,
        due_date__gte=today,
        due_date__lte=today + timedelta(days=3),
        status__in=ACTIVE_ISSUE_STATUSES,
    ).count()
    return {
        'assigned_open': assigned_open,
        'created_open': created_open,
        'overdue': overdue,
        'due_soon': due_soon,
    }


class MyIssuesView(LoginRequiredMixin, ListView):
    """«Мои замечания» (Волна 3): личный инбокс по всем проектам.

    Дайджест-сводка «на вас N, просрочено K, срок в ближайшие 3 дня»
    и таблица с фильтрами assigned/created/open/overdue.
    """

    template_name = 'issues/my_issues.html'
    context_object_name = 'issues'
    paginate_by = 50

    def get_queryset(self):
        queryset = _my_issues_base(self.request.user)
        scope = (self.request.GET.get('scope') or '').strip()
        flag = (self.request.GET.get('flag') or '').strip()

        if scope == 'assigned':
            queryset = queryset.filter(assignee=self.request.user)
        elif scope == 'created':
            queryset = queryset.filter(author=self.request.user)

        if flag == 'open':
            queryset = queryset.filter(status__in=ACTIVE_ISSUE_STATUSES)
        elif flag == 'overdue':
            queryset = queryset.filter(
                assignee=self.request.user,
                due_date__isnull=False,
                due_date__lt=timezone.localdate(),
                status__in=ACTIVE_ISSUE_STATUSES,
            )
        elif flag == 'due_soon':
            queryset = queryset.filter(
                assignee=self.request.user,
                due_date__isnull=False,
                due_date__gte=timezone.localdate(),
                due_date__lte=timezone.localdate() + timedelta(days=3),
                status__in=ACTIVE_ISSUE_STATUSES,
            )
        elif flag == 'closed':
            queryset = queryset.filter(status__in=ISSUE_STATUS_GROUPS['closed'])

        query = (self.request.GET.get('q') or '').strip()
        if query:
            queryset = queryset.filter(
                Q(description__icontains=query)
                | Q(document__code__icontains=query)
                | Q(document__name__icontains=query)
                | Q(document__project__name__icontains=query)
            )
        return queryset.order_by(
            'due_date', '-created_at'
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'counts': _my_issues_counts(self.request.user),
            'filters': {
                'scope': (self.request.GET.get('scope') or '').strip(),
                'flag': (self.request.GET.get('flag') or '').strip(),
                'q': (self.request.GET.get('q') or '').strip(),
            },
        })
        return context


# Коды статусов замечаний → семантические имена токенов (для бейджей)
STATUS_BADGE_MAP = {
    'NEW': 'wait',
    'IN_PROGRESS': 'progress',
    'DISCUSSION': 'review',
    'REJECTED': 'attention',
    'DONE': 'done',
    'CLOSED': 'closed',
    'CANCELLED': 'closed',
}


def _notification_list(request):
    """Последние уведомления пользователя для выпадающего списка."""
    return (
        Notification.objects.filter(recipient=request.user)
        .select_related('actor', 'issue', 'document')[:12]
    )


@login_required
def notifications_partial(request):
    """HTMX-фрагмент колокольчика: список + счётчик непрочитанных."""
    unread = Notification.objects.filter(
        recipient=request.user, is_read=False,
    ).count()
    return render(
        request,
        'issues/_notifications_list.html',
        {
            'notifications': _notification_list(request),
            'unread_count': unread,
        },
    )


@login_required
def notifications_read_all(request):
    """Отметить все уведомления прочитанными (htmx-post из колокольчика)."""
    if request.method == 'POST':
        Notification.objects.filter(
            recipient=request.user, is_read=False,
        ).update(is_read=True)
        if request.headers.get('HX-Request'):
            return notifications_partial(request)
        return redirect('issues:my_issues')
    return HttpResponseForbidden('POST only')
