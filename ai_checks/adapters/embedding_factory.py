"""Фабрика EmbeddingService: выбор провайдера по AI_EMBEDDING_PROVIDER.

Три провайдера:
- cloudru  → CloudRuEmbedding (Cloud.ru Foundation Models, требует AI_EMBEDDING_API_KEY);
- gigachat → GigaChatEmbedding (GigaChat Embeddings, требует AI_GIGACHAT_AUTH_KEY);
- onnx     → BgeM3OnnxEmbedding (локальный ONNX, требует AI_EMBEDDING_MODEL_DIR).

Fail-fast: неизвестный/пустой провайдер → RuntimeError.
"""

from __future__ import annotations

from django.conf import settings

from ai_checks.ports.embedding import EmbeddingService as EmbeddingServicePort


def get_embedding_service() -> EmbeddingServicePort:
    """Фабрика EmbeddingService (settings.AI_EMBEDDING_PROVIDER).

    cloudru  → CloudRuEmbedding (требует AI_EMBEDDING_API_KEY);
    gigachat → GigaChatEmbedding (требует AI_GIGACHAT_AUTH_KEY);
    onnx     → BgeM3OnnxEmbedding (требует AI_EMBEDDING_MODEL_DIR);
    иначе    → RuntimeError (fail-fast).
    """
    provider = (
        getattr(settings, 'AI_EMBEDDING_PROVIDER', '') or ''
    ).strip().lower()
    if provider == 'cloudru':
        from ai_checks.adapters.embedding_onnx import CloudRuEmbedding

        return CloudRuEmbedding()
    if provider == 'gigachat':
        from ai_checks.adapters.embedding_gigachat import GigaChatEmbedding

        return GigaChatEmbedding()
    if provider == 'onnx':
        from ai_checks.adapters.embedding_onnx import BgeM3OnnxEmbedding

        return BgeM3OnnxEmbedding()
    raise RuntimeError(
        f'AI_EMBEDDING_PROVIDER={provider!r} не поддерживается: '
        "используйте 'cloudru' (Cloud.ru), 'gigachat' (GigaChat) "
        "или 'onnx' (локальный bge-m3)."
    )
