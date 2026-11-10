"""Проверка обработки 2D-выхода ONNX-модели (bge-m3).

Этап 19: BgeM3OnnxEmbedding поддерживает и 3D (batch, seq, hidden),
и 2D (batch, hidden) — во втором случае CLS-редукция не нужна.
"""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np


def _random_batched(shape):
    """Случайный массив с ненулевой L2-нормой по последней оси."""
    arr = np.random.randn(*shape).astype(np.float32)
    return arr


class BgeM3OnnxEmbedding2DTest(unittest.TestCase):
    """Мокаем ONNX-сессию: проверяем embed_texts с 2D-выходом."""

    def _make_adapter(self, output_shape: tuple, output_dtype=np.float32):
        """Создаёт BgeM3OnnxEmbedding с мокаемой сессией."""
        from ai_checks.adapters.embedding_onnx import BgeM3OnnxEmbedding

        adapter = BgeM3OnnxEmbedding.__new__(BgeM3OnnxEmbedding)
        adapter._dim = 1024
        adapter._session = mock.MagicMock()
        adapter._output_name = 'output'
        adapter._input_names = ('input_ids', 'attention_mask')

        # Токенизатор: возвращает dict с input_ids/attention_mask
        # формы (batch_size, seq_len)
        def _tokenize(texts, **kwargs):
            batch_size = len(texts)
            return {
                'input_ids': np.zeros((batch_size, 10), dtype=np.int64),
                'attention_mask': np.ones((batch_size, 10), dtype=np.int64),
            }
        adapter._tokenizer = mock.MagicMock(side_effect=_tokenize)

        # ONNX-сессия: моканый .run() возвращает случайные векторы
        # с ненулевой L2-нормой для проверки L2-нормализации
        def _run_fn(output_names, feeds):
            batch_size = feeds['input_ids'].shape[0]
            new_shape = (batch_size,) + output_shape[1:]
            return [_random_batched(new_shape)]
        adapter._session.run = mock.MagicMock(side_effect=_run_fn)

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

    def test_embed_texts_bad_ndim(self):
        """4D+ → RuntimeError."""
        adapter = self._make_adapter((1, 2, 3, 4))
        with self.assertRaises(RuntimeError) as ctx:
            adapter.embed_texts(['bad'])
        self.assertIn('4', str(ctx.exception))

    def test_embed_texts_empty(self):
        """Пустой список → пустой результат."""
        adapter = self._make_adapter((1, 1024))
        result = adapter.embed_texts([])
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
