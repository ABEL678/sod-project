"""BgeM3OnnxEmbedding — адаптер EmbeddingService (Этап 19, Ф0.2).

Блок 4 бэта-фиксов 2026-11-08: вместо заглушки SHA-256 — реальный
ONNX-инференс bge-m3 (dense-представление, 1024, CLS-pooling +
L2-нормализация; onnxruntime CPU + токенизатор transformers).

Модель ожидается локальной папкой (settings.AI_EMBEDDING_MODEL_DIR,
по умолчанию <BASE_DIR>/ml_models/bge-m3):
- model.onnx              — экспорт BAAI/bge-m3 (ONNX, int8/fp16 опт.);
- tokenizer.json и прочие — файлы токенизатора HuggingFace.

Пока папки с моделью нет (или onnxruntime/transformers не установлены),
адаптер возвращает прежнюю детерминированную заглушку SHA-256 и логирует
warning — контур остаётся работоспособным до скачивания модели. Важно:
векторы заглушки и bge-m3 несравнимы — после включения реальной модели
нужна переиндексация («Переиндексировать проект»), model_version в БД
('bge-m3-stub-v1' / 'bge-m3-onnx-v1') показывает, чем считался чанк.
"""

from __future__ import annotations

import hashlib
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
    """bge-m3 (ONNX, CPU): CLS-pooling + L2; fallback — SHA-256 заглушка."""

    def __init__(self, dim: int = 1024, model_dir: str | None = None) -> None:
        self._dim = dim
        self._session = None
        self._input_names: tuple[str, ...] = ()
        self._output_name = ''
        self._tokenizer = None
        self._model_ready = False
        self._model_dir = self._resolve_model_dir(model_dir)
        self._load_model()

    # --- контракт EmbeddingService -----------------------------------------

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self._model_ready:
            return self._embed_onnx(texts)
        return [self._vector_stub(text) for text in texts]

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        return 'bge-m3-onnx-v1' if self._model_ready else 'bge-m3-stub-v1'

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
        """Ленивая загрузка ONNX-модели и токенизатора (без сети).

        Любая проблема (нет файла, нет зависимостей, ошибка загрузки)
        — это warning и работа на заглушке, не падение процесса.
        """
        model_path = self._model_dir / 'model.onnx'
        if not model_path.exists():
            logger.warning(
                'BgeM3OnnxEmbedding: модель bge-m3 не найдена (%s) — '
                'используется SHA-256 заглушка. Скачайте экспорт '
                'BAAI/bge-m3 в ONNX (см. README блока 4), иначе RAG-поиск '
                'будет некачественным.',
                model_path,
            )
            return
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
        except ImportError as exc:
            logger.warning(
                'BgeM3OnnxEmbedding: %s — используется SHA-256 заглушка '
                '(нужны onnxruntime и transformers из requirements.txt).',
                exc,
            )
            return
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
        except Exception as exc:  # noqa: BLE001 — деградация лучше падения
            logger.warning(
                'BgeM3OnnxEmbedding: не удалось загрузить модель (%s): %s',
                model_path, exc,
            )
            return
        self._session = session
        self._output_name = session.get_outputs()[0].name
        self._input_names = tuple(
            item.name for item in session.get_inputs()
        )
        self._tokenizer = tokenizer
        self._model_ready = True
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

    # --- прежняя заглушка (fallback) ---------------------------------------------

    def _vector_stub(self, text: str) -> list[float]:
        """Детерминированный вектор из SHA-256 (значения в [-1, 1])."""
        seed = hashlib.sha256(text.encode('utf-8')).digest()
        values = []
        for index in range(self._dim):
            byte = seed[index % len(seed)]
            values.append(round((byte / 127.5) - 1.0, 6))
        return values
