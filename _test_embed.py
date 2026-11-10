"""Быстрая проверка настроенного EmbeddingService (вне тестов Django).

Запуск из корня проекта: python _test_embed.py
Печатает провайдера (cloudru/gigachat/onnx), версию модели,
размерность и L2-норму контрольного вектора.
Fail-fast: без ключа/модели упадёт с понятной ошибкой — это норма.

Поддерживаемые провайдеры (AI_EMBEDDING_PROVIDER):
- cloudru  → Cloud.ru Foundation Models (требует AI_EMBEDDING_API_KEY);
- gigachat → GigaChat Embeddings (требует AI_GIGACHAT_AUTH_KEY);
- onnx     → локальный bge-m3 (требует AI_EMBEDDING_MODEL_DIR).
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')
django.setup()

from ai_checks.adapters.embedding_factory import get_embedding_service

# Показываем, какой провайдер выбран
from django.conf import settings
provider = getattr(settings, 'AI_EMBEDDING_PROVIDER', 'cloudru') or 'cloudru'
print(f'AI_EMBEDDING_PROVIDER={provider.strip()!r}')

try:
    service = get_embedding_service()
    result = service.embed_texts(['проверка связи'])
    print(f'провайдер: {type(service).__name__}, модель: {service.model_version}')
    print(f'вектор: {len(result[0])} измерений')
    norm = sum(v * v for v in result[0]) ** 0.5
    print(f'L2-норма: {norm:.6f}')
except RuntimeError as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
