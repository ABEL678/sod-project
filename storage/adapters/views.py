from io import BytesIO

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import (
    FileResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView
from openpyxl import Workbook
from openpyxl.styles import Font

from storage.domain.models import Document, DocumentVersion, MailRule, Project, Section
from storage.domain.services import check_read_permission, generate_vedomost, upload_document


class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = 'storage/project_list.html'
    context_object_name = 'projects'
    ordering = ['code']


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = 'storage/project_detail.html'
    context_object_name = 'project'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sections'] = self.object.sections.order_by('order')
        context['documents'] = generate_vedomost(self.object)
        return context


class DocumentDetailView(LoginRequiredMixin, DetailView):
    model = Document
    template_name = 'storage/document_detail.html'
    context_object_name = 'document'

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not check_read_permission(request.user, self.object):
            return HttpResponseForbidden('Нет прав на просмотр документа')
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['versions'] = self.object.versions.select_related('uploaded_by').order_by('-version_number')
        context['issues'] = self.object.issues.select_related('author', 'assignee', 'version').order_by('-created_at')
        return context


class DocumentUploadAjaxView(LoginRequiredMixin, View):
    def handle_no_permission(self):
        return JsonResponse({'error': 'Требуется авторизация'}, status=403)

    def post(self, request, project_id):
        project = Project.objects.filter(pk=project_id).first()
        if project is None:
            return JsonResponse({'error': 'Проект не найден'}, status=404)

        section_id = request.POST.get('section_id')
        file = request.FILES.get('file')
        if not section_id or file is None:
            return JsonResponse({'error': 'Не выбран раздел или файл'}, status=400)

        try:
            section = Section.objects.get(pk=section_id)
        except (Section.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'error': 'Раздел не найден'}, status=400)

        code = (request.POST.get('code') or file.name).strip()
        name = (request.POST.get('name') or file.name).strip()
        doc_type = request.POST.get('doc_type') or 'other'

        try:
            version = upload_document(
                user=request.user,
                project=project,
                section=section,
                code=code,
                name=name,
                doc_type=doc_type,
                file=file,
                comment=request.POST.get('comment', ''),
            )
        except (PermissionDenied, ValueError) as exc:
            return JsonResponse({'error': str(exc)}, status=400)

        return JsonResponse({'status': 'ok', 'version': version.version_number})


class DocumentDownloadView(View):
    def get(self, request, version_id):
        version = get_object_or_404(
            DocumentVersion.objects.select_related('document__project'),
            pk=version_id,
        )

        if request.user.is_authenticated and not check_read_permission(request.user, version.document):
            return HttpResponseForbidden('Нет прав на скачивание файла')

        if not version.file:
            return HttpResponseNotFound('Файл не найден')

        try:
            file_handle = version.file.open('rb')
        except FileNotFoundError:
            return HttpResponseNotFound('Файл не найден')

        filename = version.file.name.rsplit('/', 1)[-1]
        return FileResponse(file_handle, as_attachment=True, filename=filename)


class ExportVedomostView(LoginRequiredMixin, View):
    def get(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)

        documents = Document.objects.filter(project=project).select_related(
            'section',
            'current_version',
            'current_version__uploaded_by',
        ).order_by('section__order', 'code')

        section_id = request.GET.get('section_id')
        if section_id:
            try:
                documents = documents.filter(section_id=int(section_id))
            except (TypeError, ValueError):
                return HttpResponseBadRequest('Неверный идентификатор раздела')

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Ведомость'

        sheet.append(['Шифр', 'Название', 'Тип', 'Версия', 'Дата', 'Автор'])
        for cell in sheet[1]:
            cell.font = Font(bold=True)

        for row, document in enumerate(documents, start=2):
            version = document.current_version

            sheet.cell(row=row, column=1, value=document.code)

            name_cell = sheet.cell(row=row, column=2, value=document.name)
            if version is not None:
                download_url = request.build_absolute_uri(
                    reverse('storage:document_download', args=[version.pk])
                )
                name_cell.hyperlink = download_url
                name_cell.font = Font(color='0563C1', underline='single')

            sheet.cell(row=row, column=3, value=document.get_doc_type_display())
            sheet.cell(row=row, column=4, value=version.version_number if version else '-')
            sheet.cell(
                row=row,
                column=5,
                value=self._format_datetime(version.uploaded_at if version else None),
            )
            sheet.cell(row=row, column=6, value=self._format_author(version))

        for column, width in {'A': 18, 'B': 40, 'C': 22, 'D': 10, 'E': 18, 'F': 24}.items():
            sheet.column_dimensions[column].width = width

        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        return FileResponse(
            buffer,
            as_attachment=True,
            filename='vedomost.xlsx',
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    @staticmethod
    def _format_datetime(value):
        if value is None:
            return ''
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime('%d.%m.%Y %H:%M')

    @staticmethod
    def _format_author(version):
        if version is None:
            return ''
        return version.uploaded_by.get_full_name() or version.uploaded_by.username


class SimulateMailImportView(LoginRequiredMixin, View):
    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)

        rule = MailRule.objects.filter(project=project).order_by('pk').first()
        if rule is None:
            messages.error(request, 'Для проекта не настроены правила импорта из почты.')
            return redirect('storage:project_detail', pk=project.pk)

        filename = 'Исходные_данные_из_почты.pdf'
        fake_file = SimpleUploadedFile(
            filename,
            b'%PDF-1.4 simulated mail attachment',
            content_type='application/pdf',
        )

        try:
            upload_document(
                user=request.user,
                project=project,
                section=rule.section,
                code='ИЗ-ПОЧТЫ-ИД',
                name='Исходные данные из почты',
                doc_type=rule.doc_type,
                file=fake_file,
                comment='Импортировано из почты (симуляция)',
            )
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect('storage:project_detail', pk=project.pk)

        messages.success(request, 'Письмо обработано, файл добавлен.')
        return redirect('storage:project_detail', pk=project.pk)
