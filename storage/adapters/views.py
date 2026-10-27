from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.views import View
from django.views.generic import DetailView, ListView

from storage.domain.models import Project, Section
from storage.domain.services import generate_vedomost, upload_document


class ProjectListView(ListView):
    model = Project
    template_name = 'storage/project_list.html'
    context_object_name = 'projects'
    ordering = ['code']


class ProjectDetailView(DetailView):
    model = Project
    template_name = 'storage/project_detail.html'
    context_object_name = 'project'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sections'] = self.object.sections.order_by('order')
        context['documents'] = generate_vedomost(self.object)
        return context


class DocumentUploadAjaxView(View):
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
