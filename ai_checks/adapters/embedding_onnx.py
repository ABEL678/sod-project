"""Адаптеры EmbeddingService: локальный ONNX и облако Cloud.ru.

Блок 2 патча зачистки 2026-11-09: SHA-256-заглушка полностью удалена —
эмбеддинги считаются только реальным инференсом bge-m3 (dense, 1024,
L2-нормализация). Векторы заглушки были несравнимы с bge-m3 и портили
RAG-поиск: смешивание stub- и onnx-чанков в одном индексе давало
нерелевантные попадания, поэтому «тихая» деградация запрещена.

Два адаптера (выбор — фабрика get_embedding_service(), патч
2026-11-10 «переход на облачные эмбеддинги»):

- BgeM3OnnxEmbedding — локальный ONNX-инференс (CPU). Модель ожидается
  папкой (settings.AI_EMBEDDING_MODEL_DIR, по умолчанию
  <BASE_DIR>/ml_models/bge-m3): model.onnx + файлы токенизатора
  HuggingFace. Режим dev/офлайн; без модели или зависимостей
  (onnxruntime, transformers) конструктор падает с понятной ошибкой
  (RuntimeError) вместо тихой заглушки.

- CloudRuEmbedding — облачный API Cloud.ru Foundation Models
  (OpenAI-совместимый протокол, модель BAAI/bge-m3, dense 1024).
  Режим прод-сервера: локальная модель 2.2 ГБ не грузится в RAM —
  на VDS с малым объёмом памяти OOM Killer убивал Gunicorn при
  индексации/поиске. Ключ — env AI_EMBEDDING_API_KEY; без ключа
  адаптер не создаётся (fail-fast, RuntimeError).

Оба адаптера возвращают векторы в одном пространстве (bge-m3 dense,
L2) — чанки, проиндексированные локально и в облаке, сравнимы.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Any

import httpx

from django.conf import settings

from ai_checks.ports.embedding import EmbeddingService as EmbeddingServicePort

logger = logging.getLogger(__name__)

# Папка модели по умолчанию относительно BASE_DIR.
DEFAULT_MODEL_DIRNAME = 'ml_models/bge-m3'

# Предел токенизатора: чанки индексации ~1000 символов, 512 токенов
# достаточно и заметно быстрее на CPU (у bge-m3 лимит 8192).
MAX_SEQ_TOKENS = 512

# --- облачный адаптер (Cloud.ru Foundation Models) -------------------------

# Переменная окружения с API-ключом Cloud.ru Foundation Models.
CLOUD_EMBEDDING_API_KEY_ENV = 'AI_EMBEDDING_API_KEY'

# OpenAI-совместимый эндпоинт и модель Cloud.ru (ТЗ патча 2026-11-10).
DEFAULT_CLOUD_BASE_URL = 'https://foundation-models.api.cloud.ru'
DEFAULT_CLOUD_MODEL = 'BAAI/bge-m3'

# Текстов в одном запросе к API: чанки индексации ~1000 символов,
# 32 текста безопасны по размеру payload и таймауту; большие индексы
# автоматически разбиваются на несколько последовательных запросов.
CLOUD_MAX_BATCH_TEXTS = 32


class BgeM3OnnxEmbedding(EmbeddingServicePort):
    """bge-m3 (ONNX, CPU): CLS-pooling + L2-нормализация, 1024.

    Fail-fast: без model.onnx / onnxruntime / transformers — RuntimeError
    с инструкцией по установке (никаких детерминированных заглушек).
    """

    def __init__(self, dim: int = 1024, model_dir: str | None = None) -> None:
        self._dim = dim
        self._input_names: tuple[str, ...] = ()
        self._output_name = ''
        self._tokenizer = None
        self._session = None
        self._model_dir = self._resolve_model_dir(model_dir)
        self._load_model()

    # --- контракт EmbeddingService -----------------------------------------

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed_onnx(texts)

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        return 'bge-m3-onnx-v1'

    # --- загрузка модели ------------------------------------------------------

    def _resolve_model_dir(self, model_dir: str | None) -> Path:
        """Папка модели: аргумент → settings.AI_EMBEDDING_MODEL_DIR → BASE_DIR."""
        if model_dir:
            return Path(model_dir)
        configured = getattr(settings, 'AI_EMBEDDING_MODEL_DIR', None)
        if configured:
            return Path(configured)
        base_dir = getattr(settings, 'BASE_DIR', Path.cwd())
        return Path(base_dir) / DEFAULT_MODEL_DIRNAME

    def _load_model(self) -> None:
        """Загрузка ONNX-модели и токенизатора; без них — понятная ошибка.

        Fail-fast (блок 2): отсутствие модели/зависимостей — это
        RuntimeError при создании адаптера, а не молчаливая заглушка.
        Ленивый импорт onnxruntime/transformers сохранён: Django
        стартует без них, ошибка возникает только при реальном
        обращении к ИИ-контуру.
        """
        model_path = self._model_dir / 'model.onnx'
        if not model_path.exists():
            raise RuntimeError(
                'Эмбеддинги bge-m3 не настроены: не найден '
                f'{model_path}. Скачайте экспорт BAAI/bge-m3 в ONNX '
                '(model.onnx + файлы токенизатора) в эту папку или '
                'укажите путь в переменной AI_EMBEDDING_MODEL_DIR. '
                'После установки модели переиндексируйте документы '
                'проекта и базу знаний («Переиндексировать проект»).'
            )
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                'Эмбеддинги bge-m3 не настроены: не установлены '
                f'зависимости ({exc.name}). Выполните '
                'pip install onnxruntime transformers (см. requirements.txt).'
            ) from exc
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = max(2, int(os.cpu_count() or 2))
            session = ort.InferenceSession(
                str(model_path),
                sess_options=options,
                providers=['CPUExecutionProvider'],
            )
            tokenizer = AutoTokenizer.from_pretrained(
                str(self._model_dir), use_fast=True,
            )
        except Exception as exc:  # noqa: BLE001 — оборачиваем в понятную ошибку
            raise RuntimeError(
                'Эмбеддинги bge-m3 не загрузились из '
                f'{self._model_dir}: {exc}. Проверьте целостность '
                'экспорта (model.onnx совместим с установленным '
                'onnxruntime, tokenizer.json на месте).'
            ) from exc

        self._session = session
        self._output_name = session.get_outputs()[0].name
        self._input_names = tuple(
            item.name for item in session.get_inputs()
        )
        self._tokenizer = tokenizer
        logger.info(
            'BgeM3OnnxEmbedding: модель загружена (%s), выход %s',
            model_path, self._output_name,
        )

    # --- реальный инференс ------------------------------------------------------

    def _embed_onnx(self, texts: list[str]) -> list[list[float]]:
        """ONNX-инференс: токенизация → forward → CLS → L2-нормализация."""
        import numpy as np

        batch = self._tokenizer(
            list(texts),
            padding=True,
            truncation=True,
            max_length=MAX_SEQ_TOKENS,
            return_tensors='np',
        )
        feeds = {
            name: np.asarray(batch[name], dtype=np.int64)
            for name in self._input_names
            if name in batch
        }
        if 'input_ids' not in feeds or 'attention_mask' not in feeds:
            raise ValueError(
                'bge-m3 ONNX: в модели нет input_ids/attention_mask '
                f'(входы: {self._input_names})'
            )
        hidden = self._session.run([self._output_name], feeds)[0]
        hidden = np.asarray(hidden)
        if hidden.ndim == 3:
            cls = hidden[:, 0, :].astype(np.float32)
        elif hidden.ndim == 2:
            cls = hidden.astype(np.float32)
        else:
            raise RuntimeError(
                f'Неожиданная размерность выхода модели: {hidden.ndim}'
            )
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        cls = cls / norms
        vectors = [
            [round(float(value), 6) for value in row]
            for row in cls
        ]
        if len(vectors) != len(texts):
            raise ValueError(
                f'bge-m3 ONNX: число векторов {len(vectors)} != '
                f'число текстов {len(texts)}'
            )
        if vectors and len(vectors[0]) != self._dim:
            raise ValueError(
                f'bge-m3 ONNX: размерность {len(vectors[0])} != '
                f'ожидалась {self._dim} (проверьте экспорт модели)'
            )
        return vectors


class CloudRuEmbedding(EmbeddingServicePort):
    """bge-m3 через Cloud.ru Foundation Models (OpenAI-совместимый API).

    Режим прод-сервера: векторы считает облачный API, локальная
    ONNX-модель (2.2 ГБ) не нужна — на VDS с малым объёмом RAM она
    вызывала OOM Killer (Gunicorn погибал при индексации/поиске).
    Протокол тот же, что у локального адаптера: dense 1024,
    L2-нормализация — векторы обоих адаптеров сравнимы в одном индексе.

    Fail-fast: без API-ключа (env AI_EMBEDDING_API_KEY) конструктор
    падает с понятной ошибкой RuntimeError — тихой деградации нет.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = DEFAULT_CLOUD_MODEL,
        dim: int = 1024,
        timeout: float = 30.0,
        verify_ssl: bool | None = None,
    ) -> None:
        resolved_key = (
            api_key or os.environ.get(CLOUD_EMBEDDING_API_KEY_ENV, '')
        ).strip()
        if not resolved_key:
            raise RuntimeError(
                'Облачные эмбеддинги Cloud.ru не настроены: не задан '
                f'{CLOUD_EMBEDDING_API_KEY_ENV}. Укажите API-ключ Cloud.ru '
                'Foundation Models в окружении или .env '
                '(AI_EMBEDDING_API_KEY=...); если облако не нужно, удалите '
                'переменную — тогда используется локальная ONNX-модель '
                'bge-m3 (AI_EMBEDDING_MODEL_DIR).'
            )
        self._dim = dim
        self._model = model
        # Приоритет: аргумент → settings → дефолт; без /v1 — добавляется в _embed_batch.
        self._base_url = (
            base_url
            or getattr(settings, 'AI_EMBEDDING_API_BASE_URL', '')
            or DEFAULT_CLOUD_BASE_URL
        ).rstrip('/')
        self._api_key = resolved_key
        self._timeout = timeout
        self._verify_ssl = (
            verify_ssl if verify_ssl is not None
            else getattr(settings, 'AI_EMBEDDING_VERIFY_SSL', True)
        )
        logger.info(
            'CloudRuEmbedding: клиент готов (%s, модель %s, verify_ssl=%s)',
            self._base_url, model, self._verify_ssl,
        )

    # --- контракт EmbeddingService -----------------------------------------

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Векторы для каждого текста: батчи по 32 → API → L2-нормализация."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), CLOUD_MAX_BATCH_TEXTS):
            batch = list(texts[start:start + CLOUD_MAX_BATCH_TEXTS])
            vectors.extend(self._embed_batch(batch))
        if len(vectors) != len(texts):
            raise ValueError(
                'Cloud.ru эмбеддинги: число векторов '
                f'{len(vectors)} != число текстов {len(texts)}'
            )
        if vectors and len(vectors[0]) != self._dim:
            raise ValueError(
                'Cloud.ru эмбеддинги: размерность '
                f'{len(vectors[0])} != ожидалась {self._dim} '
                '(проверьте модель — нужна BAAI/bge-m3, dense 1024)'
            )
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """Один запрос /embeddings: {model, input: [...]} → data[].embedding."""
        import time

        payload = {'model': self._model, 'input': list(batch)}
        last_error: Exception | None = None
        for attempt in range(3):
            started = time.monotonic()
            try:
                with httpx.Client(
                    timeout=self._timeout,
                    verify=self._verify_ssl,
                ) as client:
                    response = client.post(
                        f'{self._base_url}/v1/embeddings',
                        headers={
                            'Authorization': f'Bearer {self._api_key}',
                            'Content-Type': 'application/json',
                        },
                        json=payload,
                    )
                    # 401/402/403 — без ретраев (ключ или баланс).
                    if response.status_code in (401, 402, 403):
                        raise RuntimeError(
                            f'Cloud.ru эмбеддинги: HTTP {response.status_code}. '
                            'Проверьте AI_EMBEDDING_API_KEY, баланс токенов и '
                            'доступ к модели BAAI/bge-m3 в Cloud.ru Foundation Models.'
                        )
                    response.raise_for_status()
                    data = response.json()
                rows = sorted(
                    data['data'],
                    key=lambda item: int(item.get('index') or 0),
                )
                vectors = [
                    self._normalize([float(value) for value in row['embedding']])
                    for row in rows
                ]
                logger.info(
                    'CloudRuEmbedding: батч из %d текстов, %.2f с',
                    len(batch), (time.monotonic() - started),
                )
                return vectors
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f'Запрос эмбеддингов Cloud.ru не удался: {last_error}')

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        return f'bge-m3-cloudru:{self._model}:v1'

    # --- служебное ----------------------------------------------------------

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        """L2-нормализация: единичная длина, как у bge-m3 (CLS-pooling)."""
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [round(value / norm, 6) for value in vector]
