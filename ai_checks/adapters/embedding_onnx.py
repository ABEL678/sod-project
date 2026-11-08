"""BgeM3OnnxEmbedding — адаптер EmbeddingService (Этап 19, Ф0.2).

Блок 2 патча зачистки 2026-11-09: SHA-256-заглушка полностью удалена —
эмбеддинги считаются только реальным ONNX-инференсом bge-m3 (dense,
1024, CLS-pooling + L2-нормализация; onnxruntime CPU + токенизатор
transformers). Векторы заглушки были несравнимы с bge-m3 и портили
RAG-поиск: смешивание stub- и onnx-чанков в одном индексе давало
нерелевантные попадания, поэтому «тихая» деградация запрещена.

Модель ожидается локальной папкой (settings.AI_EMBEDDING_MODEL_DIR,
по умолчанию <BASE_DIR>/ml_models/bge-m3):
- model.onnx              — экспорт BAAI/bge-m3 (ONNX, int8/fp16 опт.);
- tokenizer.json и прочие — файлы токенизатора HuggingFace.

Если модель или зависимости (onnxruntime, transformers) недоступны —
конструктор падает с понятной ошибкой (RuntimeError) вместо тихой
заглушки: индексация и RAG-поиск требуют реальных векторов bge-m3.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from django.conf import settings

from ai_checks.ports.embedding import EmbeddingService as EmbeddingServicePort

logger = logging.getLogger(__name__)

# Папка модели по умолчанию относительно BASE_DIR.
DEFAULT_MODEL_DIRNAME = 'ml_models/bge-m3'

# Предел токенизатора: чанки индексации ~1000 символов, 512 токенов
# достаточно и заметно быстрее на CPU (у bge-m3 лимит 8192).
MAX_SEQ_TOKENS = 512


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
        cls = np.asarray(hidden)[:, 0, :].astype(np.float32)
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
