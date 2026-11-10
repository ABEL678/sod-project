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
DEFAULT_CLOUD_BASE_URL = 'https://foundation-models.api.cloud.ru/v1'
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
        base_url: str = DEFAULT_CLOUD_BASE_URL,
        model: str = DEFAULT_CLOUD_MODEL,
        dim: int = 1024,
        timeout: float = 30.0,
        client: Any | None = None,
    ) -> None:
        """api_key/client — точки подмены для тестов; в бою оба по умолчанию.

        client — готовый OpenAI-совместимый клиент (DI вместо
        openai.OpenAI, полезно в тестах без сети). timeout — лимит на
        один запрос API: дефолт SDK (600 с) не годится для вьюх и
        Celery-воркеров — зависший запрос держит Gunicorn-воркер.
        """
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
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    'Облачные эмбеддинги Cloud.ru: не установлен пакет '
                    f'openai ({exc.name}). Выполните pip install openai '
                    '(см. requirements.txt).'
                ) from exc
            client = OpenAI(
                api_key=resolved_key, base_url=base_url, timeout=timeout,
            )
        self._client = client
        logger.info(
            'CloudRuEmbedding: клиент готов (%s, модель %s)', base_url, model,
        )

    # --- контракт EmbeddingService -----------------------------------------

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Векторы для каждого текста: батчи по 32 → API → L2-нормализация."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), CLOUD_MAX_BATCH_TEXTS):
            batch = list(texts[start:start + CLOUD_MAX_BATCH_TEXTS])
            response = self._client.embeddings.create(
                model=self._model, input=batch,
            )
            items = sorted(response.data, key=lambda item: item.index)
            if len(items) != len(batch):
                raise ValueError(
                    'Cloud.ru эмбеддинги: API вернул неверное число '
                    f'векторов {len(items)} != {len(batch)}'
                )
            vectors.extend(
                self._normalize([float(value) for value in item.embedding])
                for item in items
            )
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

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        return 'bge-m3-cloudru-v1'

    # --- служебное ----------------------------------------------------------

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        """L2-нормализация: единичная длина, как у bge-m3 (CLS-pooling)."""
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [round(value / norm, 6) for value in vector]


def get_embedding_service() -> EmbeddingServicePort:
    """Фабрика адаптера эмбеддингов по окружению.

    AI_EMBEDDING_API_KEY задан → CloudRuEmbedding (облачный API,
    режим прод-сервера без локальной модели); иначе — локальный
    BgeM3OnnxEmbedding (dev/офлайн). Переменная читается при вызове,
    а не при импорте модуля: python-dotenv наполняет os.environ на
    старте Django, а тесты подменяют окружение динамически.
    """
    api_key = (os.environ.get(CLOUD_EMBEDDING_API_KEY_ENV) or '').strip()
    if api_key:
        return CloudRuEmbedding(api_key=api_key)
    return BgeM3OnnxEmbedding()
