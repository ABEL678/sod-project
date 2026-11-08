"""Проверка обработки 2D-выхода ONNX-модели (bge-m3).

Этап 19: BgeM3OnnxEmbedding поддерживает и 3D (batch, seq, hidden),
и 2D (batch, hidden) — во втором случае CLS-редукция не нужна.
"""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np


class BgeM3OnnxEmbedding2DTest(unittest.TestCase):
    """Мокаем ONNX-сессию: проверяем embed_texts с 2D-выходом."""

    def _make_adapter(self, output_shape: tuple, output_dtype=np.float32):
        """Создаёт BgeM3OnnxEmbedding с мокаемой сессией."""
        from ai_checks.adapters.embedding_onnx import BgeM3OnnxEmbedding

        adapter = BgeM3OnnxEmbedding.__new__(BgeM3OnnxEmbedding)
        adapter._dim = 1024
        adapter._tokenizer = mock.MagicMock()
        adapter._session = mock.MagicMock()
        adapter._output_name = 'output'
        adapter._input_names = ('input_ids', 'attention_mask')

        # Токенизатор возвращает пустые словари — feeds формируются
        # только из input_names, которых нет в batch, поэтому feeds = {}.
        # Это не влияет: мокаем сессию, которая игнорирует feeds.
        adapter._tokenizer.return_value = {}

        # ONNX-сессия возвращает заданную форму
        adapter._session.run.return_value = [
            np.zeros(output_shape, dtype=output_dtype),
        ]
        return adapter

    def test_embed_texts_2d_output_single_text(self):
        """2D (1, 1024) → один вектор размерности 1024."""
        adapter = self._make_adapter((1, 1024))
        texts = ['тестовый текст']
        result = adapter.embed_texts(texts)

        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 1024)
        # L2-нормализация: норма ≈ 1.0
        import math
        norm = math.sqrt(sum(v * v for v in result[0]))
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_embed_texts_2d_output_batch(self):
        """2D (3, 1024) → три вектора."""
        adapter = self._make_adapter((3, 1024))
        texts = ['текст один', 'текст два', 'текст три']
        result = adapter.embed_texts(texts)

        self.assertEqual(len(result), 3)
        for vec in result:
            self.assertEqual(len(vec), 1024)
            norm = sum(v * v for v in vec) ** 0.5
            self.assertAlmostEqual(norm, 1.0, places=5)

    def test_embed_texts_3d_output_single_text(self):
        """3D (1, 64, 1024) → CLS ([:, 0, :]) → вектор 1024."""
        adapter = self._make_adapter((1, 64, 1024))
        texts = ['тестовый текст']
        result = adapter.embed_texts(texts)

        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 1024)
        norm = sum(v * v for v in result[0]) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_embed_texts_3d_output_batch(self):
        """3D (2, 32, 1024) → CLS → два вектора."""
        adapter = self._make_adapter((2, 32, 1024))
        texts = ['первый', 'второй']
        result = adapter.embed_texts(texts)

        self.assertEqual(len(result), 2)
        for vec in result:
            self.assertEqual(len(vec), 1024)
            norm = sum(v * v for v in vec) ** 0.5
            self.assertAlmostEqual(norm, 1.0, places=5)

    def test_embed_texts_empty(self):
        """Пустой список → пустой результат."""
        adapter = self._make_adapter((1, 1024))
        result = adapter.embed_texts([])
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
