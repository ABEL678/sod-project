"""Быстрая проверка настроенного EmbeddingService (вне тестов Django).

Запуск из корня проекта: python _test_embed.py
Печатает провайдера (GigaChatEmbedding или офлайн-фолбэк bge-m3),
версию модели, размерность и L2-норму контрольного вектора.
Fail-fast: без ключа/модели упадёт с понятной ошибкой — это норма.
"""
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')
django.setup()

from ai_checks.adapters.embedding_gigachat import get_embedding_service

service = get_embedding_service()
result = service.embed_texts(['проверка связи'])
print(f'провайдер: {type(service).__name__}, модель: {service.model_version}')
print(f'вектор: {len(result[0])} измерений')
norm = sum(v * v for v in result[0]) ** 0.5
print(f'L2-норма: {norm:.6f}')
