"""Эмбеддинги GigaChat (Сбер) — адаптер EmbeddingService + фабрика.

Зачистка 2026-11-10 («только экосистема Сбера»): эмбеддинги считаются
облачной моделью GigaChat через OpenAI-совместимый POST /v1/embeddings
(тот же Authorization Key и OAuth-токен, что у GigaChatProvider —
см. llm_http.py). Ключ и токен одни на весь ИИ-контур.

Модели эмбеддингов GigaChat (developers.sber.ru/docs/ru/gigachat):
Embeddings, Embeddings-2, Embeddings-3B-2025-09, EmbeddingsGigaR.
Выбор — settings.AI_EMBEDDING_MODEL. ВАЖНО: векторы разных моделей
несравнимы, смена модели требует полной переиндексации (индексация
пишет model_version в InternalDocument.embedding_model).

Fail-fast (как в embedding_onnx.py): без AI_GIGACHAT_AUTH_KEY —
RuntimeError при создании адаптера, «тихих» заглушек нет. Размерность
проверяется по AI_EMBEDDING_DIM (по умолчанию 1024 — колонки pgvector
VectorField(dimensions=1024), миграции 0002/0010): несовпадение факта с
настройкой = понятная ошибка вместо порчи векторного индекса.

Офлайн-фолбэк: AI_EMBEDDING_PROVIDER=onnx → BgeM3OnnxEmbedding
(bge-m3, локальная папка AI_EMBEDDING_MODEL_DIR) — фабрика
get_embedding_service() ниже.
"""

from __future__ import annotations

import logging
import time

import httpx
from django.conf import settings

from ai_checks.adapters.llm_http import GigaChatProvider
from ai_checks.ports.embedding import EmbeddingService as EmbeddingServicePort

logger = logging.getLogger(__name__)

# Доступные модели эмбеддингов GigaChat (справочно; выбор — AI_EMBEDDING_MODEL).
AVAILABLE_EMBEDDING_MODELS = (
    'Embeddings',
    'Embeddings-2',
    'Embeddings-3B-2025-09',
    'EmbeddingsGigaR',
)

# Retry ×2 + паузы ~1.5 с: индексация идёт в Celery-задаче, синхронного
# бюджета gunicorn здесь нет, поэтому таймаут полный (AI_EMBEDDING_TIMEOUT).
RETRIES = 3


class GigaChatEmbedding(EmbeddingServicePort):
    """Эмбеддинги GigaChat: OAuth-токен → POST {base}/embeddings.

    Тексты отправляются батчами по AI_EMBEDDING_BATCH_SIZE (по умолчанию
    8) — против лимитов API на размер запроса. Ответ мапится по полю
    index (порядок сохраняется независимо от порядка в data). Пустой
    список → пустой список векторов (без запроса).
    """

    name = 'gigachat-embeddings'

    def __init__(
        self,
        authorization_key: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        timeout: float | None = None,
        batch_size: int | None = None,
        verify_ssl: bool | None = None,
    ) -> None:
        self.authorization_key = (
            authorization_key
            or (getattr(settings, 'AI_GIGACHAT_AUTH_KEY', '') or '').strip()
        )
        if not self.authorization_key:
            raise RuntimeError(
                'Эмбеддинги GigaChat не настроены: пуст AI_GIGACHAT_AUTH_KEY. '
                'Задайте ключ в .env (тот же, что для LLM GigaChat) или '
                'включите офлайн-фолбэк AI_EMBEDDING_PROVIDER=onnx '
                '(bge-m3, локальная папка AI_EMBEDDING_MODEL_DIR).'
            )
        self.model = model or getattr(
            settings, 'AI_EMBEDDING_MODEL', 'Embeddings',
        )
        self._dim = int(dim or getattr(settings, 'AI_EMBEDDING_DIM', 1024))
        self.timeout = float(
            timeout or getattr(settings, 'AI_EMBEDDING_TIMEOUT', 60),
        )
        self.batch_size = max(1, int(
            batch_size or getattr(settings, 'AI_EMBEDDING_BATCH_SIZE', 8),
        ))
        self.verify_ssl = bool(
            verify_ssl if verify_ssl is not None
            else getattr(settings, 'AI_GIGACHAT_VERIFY_SSL', True),
        )
        # OAuth-токен общий с LLM-провайдером: тот же Authorization Key,
        # тот же кэш 25 мин, тот же базовый URL API.
        self._token_source = GigaChatProvider(
            self.authorization_key,
            timeout=self.timeout,
            verify_ssl=self.verify_ssl,
        )
        self.api_base_url = self._token_source.api_base_url

    # --- контракт EmbeddingService -------------------------------------

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start:start + self.batch_size])
            vectors.extend(self._embed_batch(batch))
        if len(vectors) != len(texts):
            raise ValueError(
                'Эмбеддинги GigaChat: число векторов '
                f'{len(vectors)} != число текстов {len(texts)}'
            )
        for vector in vectors:
            if len(vector) != self._dim:
                raise RuntimeError(
                    f'Эмбеддинги GigaChat: размерность вектора '
                    f'{len(vector)} != AI_EMBEDDING_DIM={self._dim}. '
                    f'Модель {self.model} выдаёт другую размерность: '
                    'задайте корректный AI_EMBEDDING_DIM и добавьте '
                    'миграцию для VectorField (pgvector), затем '
                    'переиндексируйте все документы и базу знаний.'
                )
        return vectors

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        return f'gigachat:{self.model}:v1'

    # --- HTTP ------------------------------------------------------------

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """Один запрос /embeddings: {model, input: [...]} → data[].embedding."""
        payload = {'model': self.model, 'input': list(batch)}
        last_error: Exception | None = None
        for attempt in range(RETRIES):
            started = time.monotonic()
            try:
                token = self._token_source.get_token()
                with httpx.Client(
                    timeout=self.timeout, verify=self.verify_ssl,
                ) as client:
                    response = client.post(
                        f'{self.api_base_url}/embeddings',
                        headers={
                            'Authorization': f'Bearer {token}',
                            'Content-Type': 'application/json',
                        },
                        json=payload,
                    )
                    if response.status_code == 401:
                        # Токен отозван/истёк раньше — сбрасываем кэш.
                        self._token_source.invalidate_token()
                    response.raise_for_status()
                    data = response.json()
                rows = sorted(
                    data['data'],
                    key=lambda item: int(item.get('index') or 0),
                )
                vectors = [
                    [float(value) for value in row['embedding']]
                    for row in rows
                ]
                logger.info(
                    'GigaChatEmbedding: батч из %d текстов, %.2f с '
                    '(модель %s)',
                    len(batch),
                    (time.monotonic() - started),
                    self.model,
                )
                return vectors
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                last_error = exc
                if attempt < RETRIES - 1:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(
            f'Запрос эмбеддингов GigaChat не удался: {last_error}'
        )


def get_embedding_service() -> EmbeddingServicePort:
    """Фабрика EmbeddingService (settings.AI_EMBEDDING_PROVIDER).

    'gigachat' (по умолчанию) — облако Сбера (GigaChatEmbedding);
    'onnx' — офлайн-фолбэк bge-m3 (BgeM3OnnxEmbedding, локальная папка
    AI_EMBEDDING_MODEL_DIR, требует onnxruntime + transformers).
    Неизвестное значение — RuntimeError (fail-fast, без «тихих»
    заглушек: векторы заглушки несравнимы с реальными и портят RAG).
    """
    provider = (
        getattr(settings, 'AI_EMBEDDING_PROVIDER', '') or 'gigachat'
    ).strip().lower()
    if provider == 'gigachat':
        return GigaChatEmbedding()
    if provider == 'onnx':
        from ai_checks.adapters.embedding_onnx import BgeM3OnnxEmbedding

        return BgeM3OnnxEmbedding()
    raise RuntimeError(
        f'AI_EMBEDDING_PROVIDER={provider!r} не поддерживается: '
        "используйте 'gigachat' (облако Сбера) или 'onnx' (офлайн bge-m3)."
    )
