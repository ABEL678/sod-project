"""Ловим стеки вызовов повторяющихся запросов Project.objects.get."""
import traceback

from django.conf import settings
from django.db import connection
from django.test import Client
from django.urls import reverse

from storage.management.commands.create_demo_project import Command

settings.DEBUG = True
cmd = Command()
cmd._created_files = []
cmd._accounts = []
cmd._create_demo(company_id=None, username=None, reset=True)

from django.contrib.auth import get_user_model
from storage.domain.models import Project

User = get_user_model()
gip = User.objects.get(username='demo_gip')
demo = Project.objects.get(code='DEMO-2026')

seen = set()

def tracer(execute, sql, params, many, context):
    if 'FROM "storage_folder" WHERE "storage_folder"."id"' in sql and len(seen) < 3:
        stack = [
            frame for frame in traceback.extract_stack()
            if ('tables' in frame.filename or 'django_scotty' in frame.filename or 'template' in frame.filename.lower())
        ]
        key = tuple((f.filename.split('/sod-wt5/')[-1], f.lineno, f.name) for f in stack[-10:])
        if key not in seen:
            seen.add(key)
            print('=== stack for project get ===')
            for f in reversed(stack[-10:]):
                print(f'  {f.filename.split("/sod-wt5/")[-1]}:{f.lineno} in {f.name}')
    return execute(sql, params, many, context)

client = Client(HTTP_HOST='localhost')
client.force_login(gip)
url = reverse('storage:project_detail', args=[demo.pk])
with connection.execute_wrapper(tracer):
    client.get(url)
print('done')
