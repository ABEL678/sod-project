"""Нагрузочные замеры СОД на демо-данных (для отчёта Аудит 5.0).

Запуск: python manage.py shell < scripts/perf_probe.py
Создаёт демо-проект, замеряет запросы/время ключевых страниц,
увеличивая объём данных (10/50/150 документов).
"""
import io
import time

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, reset_queries
from django.test import Client
from django.urls import reverse

from storage.domain.models import Document
from storage.management.commands.create_demo_project import Command
from storage.domain.services import upload_document

PDF_BYTES = (
    b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
    b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
    b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]>>endobj\n'
    b'xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\n%%EOF'
)


def measure(client, url, label):
    reset_queries()
    start = time.perf_counter()
    response = client.get(url)
    elapsed = (time.perf_counter() - start) * 1000
    queries = len(connection.queries)
    status = response.status_code
    print(f'{label:38s} {status}  {elapsed:8.1f} ms  {queries:4d} queries')
    return elapsed, queries


def main():
    settings.DEBUG = True  # для recording connection.queries
    cmd = Command()
    cmd._created_files = []
    cmd._accounts = []
    cmd._create_demo(company_id=None, username=None, reset=True)

    from storage.domain.models import Company, Project, Section, UserProfile, ProjectMembership
    from django.contrib.auth import get_user_model

    User = get_user_model()
    gip = User.objects.get(username='demo_gip')
    company = Company.objects.first()
    project = Project.objects.exclude(code='DEMO-2026').first() or Project.objects.filter(code='DEMO-2026').first()
    demo = Project.objects.get(code='DEMO-2026')
    section = Section.objects.filter(project=demo).first()

    client = Client(HTTP_HOST='localhost')
    client.force_login(gip)

    detail_url = reverse('storage:project_detail', args=[demo.pk])
    dashboard_url = reverse('storage:dashboard')
    search_url = reverse('storage:search', args=[demo.pk]) + '?q=лист'

    doc = Document.objects.filter(project=demo).select_related('current_version').first()
    document_url = reverse('storage:document_detail', args=[doc.pk])

    print('=== базовый прогон (демо-проект, ~40 документов) ===')
    measure(client, dashboard_url, 'Дашборд')
    measure(client, detail_url, 'Страница проекта (реестр)')
    measure(client, detail_url + '?tab=documents&zone=PUBLISHED', 'Реестр + зона PUBLISHED')
    measure(client, detail_url + '?tab=documents&status=in_review', 'Реестр + фильтр статуса')
    measure(client, search_url, 'Поиск по проекту')
    measure(client, document_url, 'Карточка документа (вьювер)')

    print('\n=== разгон: добавляем документы в демо-проект ===')
    for target in (100, 300):
        current = Document.objects.filter(project=demo).count()
        add = max(0, target - current)
        created = 0
        for index in range(add):
            code = f'LOAD-{target}-{index:04d}'
            try:
                upload_document(
                    user=gip,
                    project=demo,
                    section=section,
                    code=code,
                    name=f'Нагрузочный документ {index}',
                    doc_type='drawing',
                    file=SimpleUploadedFile(f'{code}.pdf', PDF_BYTES),
                )
                created += 1
            except Exception:
                break
        print(f'--- документов всего: {Document.objects.filter(project=demo).count()} '
              f'(добавлено {created}) ---')
        measure(client, dashboard_url, 'Дашборд')
        measure(client, detail_url, 'Страница проекта (реестр)')
        measure(client, search_url, 'Поиск по проекту')
        measure(client, document_url, 'Карточка документа')


# Запускается через `manage.py shell < scripts/perf_probe.py`:
# __name__ там не '__main__', поэтому вызываем напрямую.
main()
