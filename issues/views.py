import json
import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import CreateView

from storage.adapters.views import _issue_payload
from storage.domain.services import (
    allowed_issue_statuses,
    check_issue_permission,
    check_read_permission,
    get_accessible_documents,
    create_issue,
    delete_issue,
    update_issue_answer,
    update_issue_status,
)

from issues.models import Issue, IssueComment


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


class IssueCreateView(CreateView):
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
        actual_fix_date = parse_date(
            self.request.POST.get('actual_fix_date') or ''
        )

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
                actual_fix_date=actual_fix_date,
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
    profile = getattr(request.user, 'userprofile', None)
    is_admin = profile is not None and profile.role == 'admin'
    if request.user == issue.author or is_admin or request.user.is_superuser:
        if issue.crop_image:
            issue.crop_image.delete(save=False)
            issue.crop_image = None
            issue.save()
    return redirect(request.META.get('HTTP_REFERER', 'storage:project_list'))


@login_required
def add_comment(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        if text:
            IssueComment.objects.create(
                issue=issue, author=request.user, text=text
            )
    return redirect(
        request.META.get('HTTP_REFERER', 'storage:project_list')
    )
