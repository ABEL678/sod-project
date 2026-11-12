"""Celery-приложение проекта (Этап 19: ИИ-контур).

Брокер — Redis (docker-compose.dev.yml). В dev/тестах по умолчанию
CELERY_TASK_ALWAYS_EAGER=True (задача выполняется синхронно, брокер
не требуется); прод включает очередь через env:
CELERY_TASK_ALWAYS_EAGER=false + CELERY_BROKER_URL=redis://...

ВАЖНО: .env загружается ДО импорта Celery, чтобы systemd-проблемы
с EnvironmentFile не ломали чтение AI_EMBEDDING_PROVIDER и других
переменных в воркере.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# --- Загрузка .env ДО создания Celery-приложения ---
_BASE_DIR = Path(__file__).resolve().parent.parent
_dotenv_path = _BASE_DIR / '.env'
if _dotenv_path.exists():
    load_dotenv(_dotenv_path, override=True)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')

from celery import Celery

app = Celery('sod_core')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# @shared_task-задачи должны использовать ЭТО приложение (иначе они
# биндятся к дефолтному Celery без наших настроек: eager-режим теряется).
app.set_default()
