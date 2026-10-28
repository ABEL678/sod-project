from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import CreateView

from storage.domain.services import (
    check_read_permission,
    get_accessible_documents,
    get_user_company,
)

from issues.models import Issue


class IssueCreateView(CreateView):
    model = Issue
    fields = ['description']
    http_method_names = ['post']

    def dispatch(self, request, *args, **kwargs):
        self.document = get_object_or_404(
            get_accessible_documents(request.user),
            pk=kwargs['document_id'],
        )
        if not check_read_permission(request.user, self.document):
            return HttpResponseForbidden('Нет прав на добавление замечания')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        issue = form.save(commit=False)
        issue.document = self.document
        issue.author = self.request.user

        version_id = self.request.POST.get('version_id')
        if version_id:
            issue.version = self.document.versions.filter(pk=version_id).first()
        if issue.version is None:
            issue.version = self.document.current_version

        issue.save()
        messages.success(self.request, 'Замечание добавлено.')
        return redirect('storage:document_detail', pk=self.document.pk)

    def form_invalid(self, form):
        messages.error(self.request, 'Введите описание замечания.')
        return redirect('storage:document_detail', pk=self.document.pk)


class IssueUpdateStatusView(View):
    def post(self, request, issue_id):
        if not request.user.is_authenticated:
            return HttpResponseForbidden('Нет прав на изменение замечания')

        issues = Issue.objects.select_related('document__project', 'author', 'assignee')

        if not request.user.is_superuser:
            company = get_user_company(request.user)
            if company is None:
                return HttpResponseForbidden('Нет прав на изменение замечания')
            issues = issues.filter(document__project__company=company)

        issue = get_object_or_404(issues, pk=issue_id)

        if not check_read_permission(request.user, issue.document):
            return HttpResponseForbidden('Нет прав на изменение замечания')

        if not self._can_manage(request.user, issue):
            return HttpResponseForbidden('Нет прав на изменение замечания')

        action = request.POST.get('action')
        if action == 'open':
            issue.status = 'open'
        elif action == 'close':
            issue.status = 'closed'
        else:
            issue.status = 'closed' if issue.status != 'closed' else 'open'

        issue.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Статус замечания обновлён.')
        return redirect('storage:document_detail', pk=issue.document_id)

    @staticmethod
    def _can_manage(user, issue):
        if user.pk in (issue.author_id, issue.assignee_id):
            return True

        profile = getattr(user, 'userprofile', None)
        if profile is None:
            return False

        if profile.role == 'admin':
            return True

        if profile.role == 'gip':
            project = issue.document.project
            if project.gip_id == user.pk:
                return True
            return project.memberships.filter(user=user, role_in_project='gip').exists()

        return False
