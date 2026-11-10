"""Облачный адаптер эмбеддингов Cloud.ru (патч «облачные эмбеддинги»).

Проверяем контракт EmbeddingService у CloudRuEmbedding: fail-fast без
ключа, L2-нормализация, размерность, порядок и число векторов,
батчирование по 32 текста, выбор адаптера фабрикой
get_embedding_service(). Сеть не используется — вместо openai.OpenAI
подставляется фейковый OpenAI-совместимый клиент (DI через client=).
"""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest import mock

from ai_checks.adapters.embedding_onnx import (
    CLOUD_EMBEDDING_API_KEY_ENV,
    DEFAULT_CLOUD_BASE_URL,
    DEFAULT_CLOUD_MODEL,
    CloudRuEmbedding,
    get_embedding_service,
)


class FakeEmbeddingsAPI:
    """Фейковый OpenAI-совместимый клиент (только embeddings.create).

    Отдаёт ненормализованный вектор [3, 0, 0, ...] длины dim для
    каждого текста — тест видит, что адаптер сам делает L2-нормализацию
    (результат [1, 0, 0, ...]). count_delta имитирует недобросовестный
    ответ API (для проверки валидации числа векторов); create_calls
    записывает параметры каждого запроса (проверка модели и батчей).
    """

    def __init__(self, dim: int = 4, count_delta: int = 0) -> None:
        self._dim = dim
        self._count_delta = count_delta
        self.create_calls: list[dict] = []
        # Как в openai.OpenAI: метод create живёт на client.embeddings.
        self.embeddings = SimpleNamespace(create=self._create)

    def _create(self, *, model: str, input: list[str]):  # noqa: A002 — как в SDK
        self.create_calls.append({'model': model, 'input': list(input)})
        raw = [3.0] + [0.0] * (self._dim - 1)
        count = max(0, len(input) + self._count_delta)
        data = [
            SimpleNamespace(embedding=raw, index=index)
            for index in range(count)
        ]
        return SimpleNamespace(data=data)


def _cloud_adapter(dim: int = 4, **kwargs):
    """CloudRuEmbedding с фейковым клиентом: без сети, ключ фиксированный."""
    client = FakeEmbeddingsAPI(dim=dim)
    return (
        CloudRuEmbedding(api_key='test-key', dim=dim, client=client, **kwargs),
        client,
    )


class CloudRuEmbeddingContractTest(unittest.TestCase):
    """CloudRuEmbedding: fail-fast, нормализация, батчи, валидация."""

    def test_fail_fast_without_key(self):
        """Без AI_EMBEDDING_API_KEY конструктор падает с RuntimeError."""
        for env_value in ('', '   '):
            with self.subTest(env_value=env_value):
                with mock.patch.dict(
                    'os.environ', {CLOUD_EMBEDDING_API_KEY_ENV: env_value},
                ):
                    with self.assertRaises(RuntimeError) as ctx:
                        CloudRuEmbedding()
                self.assertIn('AI_EMBEDDING_API_KEY', str(ctx.exception))

    def test_embed_texts_normalizes_and_keeps_order(self):
        """L2-нормализация, сохранение порядка и метаданные контракта."""
        adapter, client = _cloud_adapter()
        vectors = adapter.embed_texts(['текст один', 'текст два'])
        self.assertEqual(len(vectors), 2)
        for vector in vectors:
            norm = math.sqrt(sum(value * value for value in vector))
            self.assertAlmostEqual(norm, 1.0, places=5)
        self.assertEqual(vectors[0][0], 1.0)
        self.assertEqual(adapter.dim, 4)
        self.assertEqual(adapter.model_version, 'bge-m3-cloudru-v1')
        self.assertEqual(client.create_calls[0]['model'], 'BAAI/bge-m3')

    def test_embed_texts_empty_input(self):
        """Пустой список → пустой ответ, без обращения к API."""
        adapter, client = _cloud_adapter()
        self.assertEqual(adapter.embed_texts([]), [])
        self.assertEqual(client.create_calls, [])

    def test_large_input_is_batched(self):
        """40 текстов → 2 запроса (батч 32 + 8), порядок сохранён."""
        adapter, client = _cloud_adapter()
        texts = [f'текст {index}' for index in range(40)]
        vectors = adapter.embed_texts(texts)
        self.assertEqual(len(vectors), 40)
        self.assertEqual(len(client.create_calls), 2)
        self.assertEqual(len(client.create_calls[0]['input']), 32)
        self.assertEqual(client.create_calls[1]['input'], texts[32:])

    def test_api_wrong_count_raises(self):
        """API вернул не все векторы → ValueError (fail-fast)."""
        client = FakeEmbeddingsAPI(dim=4, count_delta=-1)
        adapter = CloudRuEmbedding(
            api_key='test-key', dim=4, client=client,
        )
        with self.assertRaises(ValueError):
            adapter.embed_texts(['а', 'б'])

    def test_api_wrong_dim_raises(self):
        """Размерность вектора не 1024 → ValueError (fail-fast)."""
        client = FakeEmbeddingsAPI(dim=3)  # адаптер ждёт 1024 (dim по умолчанию)
        adapter = CloudRuEmbedding(api_key='test-key', client=client)
        with self.assertRaises(ValueError):
            adapter.embed_texts(['а'])

    def test_defaults_match_cloud_ru_spec(self):
        """Эндпоинт и модель по умолчанию — по ТЗ Cloud.ru Foundation Models."""
        self.assertEqual(
            DEFAULT_CLOUD_BASE_URL,
            'https://foundation-models.api.cloud.ru/v1',
        )
        self.assertEqual(DEFAULT_CLOUD_MODEL, 'BAAI/bge-m3')


class GetEmbeddingServiceTest(unittest.TestCase):
    """Фабрика get_embedding_service(): облако по ключу, иначе ONNX."""

    def test_with_key_returns_cloud_adapter(self):
        """AI_EMBEDDING_API_KEY задан → CloudRuEmbedding(api_key=...)."""
        env = {CLOUD_EMBEDDING_API_KEY_ENV: 'key-from-env'}
        with mock.patch.dict('os.environ', env):
            with mock.patch(
                'ai_checks.adapters.embedding_onnx.CloudRuEmbedding',
            ) as cloud_cls:
                service = get_embedding_service()
        self.assertIs(service, cloud_cls.return_value)
        cloud_cls.assert_called_once_with(api_key='key-from-env')

    def test_without_key_returns_onnx_adapter(self):
        """Ключ пуст → локальный BgeM3OnnxEmbedding (dev-режим)."""
        env = {CLOUD_EMBEDDING_API_KEY_ENV: ''}
        with mock.patch.dict('os.environ', env):
            with mock.patch(
                'ai_checks.adapters.embedding_onnx.BgeM3OnnxEmbedding',
            ) as onnx_cls:
                service = get_embedding_service()
        self.assertIs(service, onnx_cls.return_value)
        onnx_cls.assert_called_once_with()
