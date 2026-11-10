"""Тесты CloudRuEmbedding (без сети).

Зачистка 2026-11-10: мокается httpx.Client.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from ai_checks.adapters.embedding_onnx import CloudRuEmbedding


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeClient:
    posts: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def post(self, url, headers=None, json=None):
        FakeClient.posts.append({
            'url': url,
            'headers': headers or {},
            'json': json,
        })
        texts = json.get('input', []) if json else []
        return FakeResponse({
            'data': [
                {
                    'index': i,
                    'embedding': [0.1] * 1024,
                }
                for i in range(len(texts))
            ],
        })


class CloudRuEmbeddingTests(SimpleTestCase):
    def setUp(self):
        FakeClient.posts = []

    @override_settings(AI_EMBEDDING_API_KEY='test-key')
    def test_embed_texts_returns_vectors(self):
        with patch('ai_checks.adapters.embedding_onnx.httpx.Client', FakeClient):
            svc = CloudRuEmbedding()
            result = svc.embed_texts(['текст 1', 'текст 2'])

        self.assertEqual(len(result), 2)
        self.assertEqual(len(result[0]), 1024)

    @override_settings(AI_EMBEDDING_API_KEY='test-key')
    def test_empty_texts_returns_empty(self):
        with patch('ai_checks.adapters.embedding_onnx.httpx.Client', FakeClient):
            svc = CloudRuEmbedding()
            result = svc.embed_texts([])

        self.assertEqual(result, [])

    @override_settings(AI_EMBEDDING_API_KEY='test-key')
    def test_model_version(self):
        with patch('ai_checks.adapters.embedding_onnx.httpx.Client', FakeClient):
            svc = CloudRuEmbedding()
        self.assertIn('cloudru', svc.model_version)

    @override_settings(AI_EMBEDDING_API_KEY='test-key')
    def test_dim_property(self):
        with patch('ai_checks.adapters.embedding_onnx.httpx.Client', FakeClient):
            svc = CloudRuEmbedding()
        self.assertEqual(svc.dim, 1024)

    @override_settings(AI_EMBEDDING_API_KEY='test-key')
    def test_dim_custom(self):
        with patch('ai_checks.adapters.embedding_onnx.httpx.Client', FakeClient):
            svc = CloudRuEmbedding(dim=1536)
        self.assertEqual(svc.dim, 1536)

    def test_no_key_raises(self):
        import os
        with patch.dict(os.environ, {'AI_EMBEDDING_API_KEY': ''}):
            with self.assertRaises(RuntimeError) as ctx:
                CloudRuEmbedding()
            self.assertIn('AI_EMBEDDING_API_KEY', str(ctx.exception))

    @override_settings(AI_EMBEDDING_API_KEY='test-key')
    def test_402_raises_without_retry(self):
        import httpx

        class Fake402Response:
            status_code = 402

            def raise_for_status(self):
                pass

            def json(self):
                return {}

        class Fake402Client:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def post(self, url, headers=None, json=None):
                return Fake402Response()

        with patch('ai_checks.adapters.embedding_onnx.httpx.Client', Fake402Client):
            svc = CloudRuEmbedding()
            with self.assertRaises(RuntimeError) as ctx:
                svc.embed_texts(['текст'])
            self.assertIn('402', str(ctx.exception))

    @override_settings(AI_EMBEDDING_API_KEY='test-key')
    def test_base_url_from_settings(self):
        with patch('ai_checks.adapters.embedding_onnx.httpx.Client', FakeClient):
            svc = CloudRuEmbedding(
                base_url='https://custom.api.cloud.ru',
            )
        self.assertEqual(svc._base_url, 'https://custom.api.cloud.ru')
        self.assertEqual(svc._verify_ssl, True)

    @override_settings(AI_EMBEDDING_API_KEY='test-key', AI_EMBEDDING_VERIFY_SSL=False)
    def test_verify_ssl_from_settings(self):
        with patch('ai_checks.adapters.embedding_onnx.httpx.Client', FakeClient):
            svc = CloudRuEmbedding()
        self.assertFalse(svc._verify_ssl)
