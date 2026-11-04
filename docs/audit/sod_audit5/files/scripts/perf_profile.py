"""Профилировщик N+1 реестра: топ повторяющихся SQL-запросов."""
from collections import Counter

from django.conf import settings
from django.db import connection, reset_queries
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

client = Client(HTTP_HOST='localhost')
client.force_login(gip)
url = reverse('storage:project_detail', args=[demo.pk])

reset_queries()
client.get(url)
patterns = Counter()
for q in connection.queries:
    sql = q['sql']
    # нормализуем литералы
    key = sql
    for ch in '0123456789':
        key = key.replace(f' = {ch}', ' = N').replace(f'({ch}', '(N')
    patterns[key] += 1

print(f'total queries: {len(connection.queries)}')
print('--- top-8 patterns ---')
for key, count in patterns.most_common(8):
    tail = key[key.index(' WHERE '):][:200] if ' WHERE ' in key else ''
    print(f'{count:4d}x  {tail}')
