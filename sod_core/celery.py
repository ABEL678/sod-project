"""Celery-приложение проекта (Этап 19: ИИ-контур).

Брокер — Redis (docker-compose.dev.yml). В dev/тестах по умолчанию
CELERY_TASK_ALWAYS_EAGER=True (задача выполняется синхронно, брокер
не требуется); прод включает очередь через env:
CELERY_TASK_ALWAYS_EAGER=false + CELERY_BROKER_URL=redis://...
"""

import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')

app = Celery('sod_core')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# @shared_task-задачи должны использовать ЭТО приложение (иначе они
# биндятся к дефолтному Celery без наших настроек: eager-режим теряется).
app.set_default()
