"""Тесты GigaChatEmbedding и фабрики get_embedding_service (без сети).

Зачистка 2026-11-10: мокаются httpx.Client и GigaChatProvider.
"""

import os
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from ai_checks.adapters.embedding_gigachat import GigaChatEmbedding
from ai_checks.adapters.embedding_factory import get_embedding_service
from ai_checks.adapters.embedding_onnx import CloudRuEmbedding


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            request = httpx.Request('POST', 'http://fake')
            raise httpx.HTTPStatusError(
                f'HTTP {self.status_code}',
                request=request,
                response=httpx.Response(
                    self.status_code, request=request,
                ),
            )

    def json(self):
        return self._data


class FakeEmbedClient:
    posts: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def post(self, url, headers=None, json=None):
        FakeEmbedClient.posts.append({
            'url': url,
            'headers': headers or {},
            'json': json,
        })
        texts = json.get('input', []) if json else []
        return FakeEmbedResponse({
            'data': [
                {
                    'index': i,
                    'embedding': [0.1] * 1024,
                }
                for i in range(len(texts))
            ],
        })


class FakeEmbedResponse:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def fake_oauth_post(url, headers=None, data=None, timeout=None, verify=None):
    return FakeResponse({
        'access_token': 'giga-token-1',
        'expires_at': 4102444800000,
    }, status_code=200)


class GigaChatEmbeddingTests(SimpleTestCase):
    def setUp(self):
        FakeEmbedClient.posts = []

    @override_settings(AI_GIGACHAT_AUTH_KEY='test-key')
    def test_embed_texts_returns_vectors(self):
        with patch('ai_checks.adapters.embedding_gigachat.httpx.post', side_effect=fake_oauth_post), \
                patch('ai_checks.adapters.embedding_gigachat.httpx.Client', FakeEmbedClient):
            svc = GigaChatEmbedding()
            result = svc.embed_texts(['текст 1', 'текст 2'])

        self.assertEqual(len(result), 2)
        self.assertEqual(len(result[0]), 1024)

    @override_settings(AI_GIGACHAT_AUTH_KEY='test-key')
    def test_empty_texts_returns_empty(self):
        with patch('ai_checks.adapters.embedding_gigachat.httpx.post', side_effect=fake_oauth_post), \
                patch('ai_checks.adapters.embedding_gigachat.httpx.Client', FakeEmbedClient):
            svc = GigaChatEmbedding()
            result = svc.embed_texts([])

        self.assertEqual(result, [])

    @override_settings(AI_GIGACHAT_AUTH_KEY='test-key')
    def test_model_version(self):
        with patch('ai_checks.adapters.embedding_gigachat.httpx.post', side_effect=fake_oauth_post), \
                patch('ai_checks.adapters.embedding_gigachat.httpx.Client', FakeEmbedClient):
            svc = GigaChatEmbedding()
        self.assertIn('gigachat', svc.model_version)

    @override_settings(AI_GIGACHAT_AUTH_KEY='test-key')
    def test_dim_property(self):
        with patch('ai_checks.adapters.embedding_gigachat.httpx.post', side_effect=fake_oauth_post), \
                patch('ai_checks.adapters.embedding_gigachat.httpx.Client', FakeEmbedClient):
            svc = GigaChatEmbedding()
        self.assertEqual(svc.dim, 1024)

    @override_settings(AI_GIGACHAT_AUTH_KEY='')
    def test_no_key_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            GigaChatEmbedding()
        self.assertIn('AI_GIGACHAT_AUTH_KEY', str(ctx.exception))


class GetEmbeddingServiceTests(SimpleTestCase):
    @override_settings(AI_EMBEDDING_PROVIDER='cloudru', AI_EMBEDDING_API_KEY='k')
    def test_factory_returns_cloudru(self):
        with patch('ai_checks.adapters.embedding_onnx.httpx.Client', FakeEmbedClient):
            svc = get_embedding_service()
        self.assertIsInstance(svc, CloudRuEmbedding)

    @override_settings(AI_EMBEDDING_PROVIDER='gigachat', AI_GIGACHAT_AUTH_KEY='k')
    def test_factory_returns_gigachat(self):
        with patch('ai_checks.adapters.embedding_gigachat.httpx.post', side_effect=fake_oauth_post), \
                patch('ai_checks.adapters.embedding_gigachat.httpx.Client', FakeEmbedClient):
            svc = get_embedding_service()
        self.assertIsInstance(svc, GigaChatEmbedding)

    @override_settings(AI_EMBEDDING_PROVIDER='onnx')
    def test_factory_returns_onnx(self):
        from ai_checks.adapters.embedding_onnx import BgeM3OnnxEmbedding

        with patch.object(BgeM3OnnxEmbedding, '__init__', return_value=None):
            svc = get_embedding_service()
        self.assertIsInstance(svc, BgeM3OnnxEmbedding)

    @override_settings(AI_EMBEDDING_PROVIDER='unknown')
    def test_factory_unknown_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            get_embedding_service()
        self.assertIn('unknown', str(ctx.exception))

    @override_settings(AI_EMBEDDING_PROVIDER='')
    def test_factory_empty_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            get_embedding_service()
        self.assertIn("не поддерживается", str(ctx.exception))
