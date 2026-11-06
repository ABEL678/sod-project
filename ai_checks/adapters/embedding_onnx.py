"""BgeM3OnnxEmbedding — адаптер EmbeddingService (Этап 19, Ф0.2).

TODO(Ф0.2-следующий шаг): подключить реальную ONNX-модель bge-m3
(int8, CPU, onnxruntime). Сейчас — детерминированная заглушка из
SHA-256 (без сети и без внешних моделей), совместимая по контракту:
dim = 1024, model_version = 'bge-m3-v1'.
"""

from __future__ import annotations

import hashlib

from ai_checks.ports.embedding import EmbeddingService as EmbeddingServicePort


class BgeM3OnnxEmbedding(EmbeddingServicePort):
    """Заглушка bge-m3: детерминированные псевдо-векторы."""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # TODO: заменить на реальный ONNX-инференс bge-m3 (int8, CPU).
        return [self._vector(text) for text in texts]

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        return 'bge-m3-v1'

    def _vector(self, text: str) -> list[float]:
        """Детерминированный вектор из SHA-256 (значения в [-1, 1])."""
        seed = hashlib.sha256(text.encode('utf-8')).digest()
        values = []
        for index in range(self._dim):
            byte = seed[index % len(seed)]
            values.append(round((byte / 127.5) - 1.0, 6))
        return values
