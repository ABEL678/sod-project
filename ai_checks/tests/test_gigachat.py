"""GigaChat-провайдер: OAuth-токен, кэш 25 мин, OpenAI-совместимый запрос.

Сеть не используется: httpx.post (OAuth) и httpx.Client (completions)
мокаются фейками, которые пишут вызовы.
"""

import uuid
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

import ai_checks.adapters.llm_http as llm_http
from ai_checks.adapters.llm_http import (
    GigaChatProvider,
    StubLLMProvider,
    get_llm_provider,
)


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


class FakeClient:
    posts: list[dict] = []
    completions_status = 200

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def post(self, url, headers=None, json=None, data=None):
        FakeClient.posts.append({
            'url': url,
            'headers': headers or {},
            'json': json,
            'data': data,
        })
        return FakeResponse(
            {
                'choices': [{'message': {'content': 'Ответ GigaChat'}}],
                'model': (json or {}).get('model', 'GigaChat-2'),
                'usage': {'prompt_tokens': 7, 'completion_tokens': 11},
                'id': 'chat-1',
            },
            status_code=FakeClient.completions_status,
        )


def fake_oauth_post(url, headers=None, data=None, timeout=None, verify=None):
    FakeClient.posts.append({
        'url': url,
        'headers': headers or {},
        'json': None,
        'data': data,
    })
    return FakeResponse({
        'access_token': 'giga-token-1',
        'expires_at': 4102444800000,
    })


class GigaChatProviderTests(SimpleTestCase):
    def setUp(self):
        FakeClient.posts = []
        FakeClient.completions_status = 200

    def _provider(self):
        return GigaChatProvider(
            'base64-authorization-key',
            model='GigaChat-2-Pro',
            timeout=30,
        )

    def test_token_requested_once_and_cached(self):
        with patch.object(llm_http.httpx, 'post', side_effect=fake_oauth_post), \
                patch.object(llm_http.httpx, 'Client', FakeClient):
            provider = self._provider()
            first = provider.complete(
                [{'role': 'user', 'content': 'Привет'}],
            )
            second = provider.complete(
                [{'role': 'user', 'content': 'Ещё раз'}],
            )

        self.assertEqual(first.text, 'Ответ GigaChat')
        self.assertEqual(first.model, 'GigaChat-2-Pro')
        self.assertEqual(first.prompt_tokens, 7)
        self.assertEqual(first.completion_tokens, 11)
        self.assertEqual(second.text, 'Ответ GigaChat')

        oauth_calls = [
            call for call in FakeClient.posts if 'oauth' in call['url']
        ]
        chat_calls = [
            call for call in FakeClient.posts if 'chat/completions' in call['url']
        ]
        self.assertEqual(len(oauth_calls), 1, 'токен должен запрашиваться один раз')
        self.assertEqual(len(chat_calls), 2)

        oauth = oauth_calls[0]
        self.assertEqual(
            oauth['url'], 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth',
        )
        self.assertEqual(
            oauth['headers']['Authorization'],
            'Bearer base64-authorization-key',
        )
        self.assertEqual(
            oauth['headers']['Content-Type'],
            'application/x-www-form-urlencoded',
        )
        uuid.UUID(oauth['headers']['RqUID'])  # валидный UUID4
        self.assertEqual(oauth['data'], {'scope': 'GIGACHAT_API_PERS'})

        chat = chat_calls[0]
        self.assertEqual(chat['url'], 'https://api.giga.chat/v1/chat/completions')
        self.assertEqual(
            chat['headers']['Authorization'], 'Bearer giga-token-1',
        )
        self.assertEqual(chat['json']['model'], 'GigaChat-2-Pro')
        self.assertEqual(
            chat['json']['messages'],
            [{'role': 'user', 'content': 'Привет'}],
        )

    def test_expired_token_is_refreshed(self):
        with patch.object(llm_http.httpx, 'post', side_effect=fake_oauth_post), \
                patch.object(llm_http.httpx, 'Client', FakeClient):
            provider = self._provider()
            provider.complete([{'role': 'user', 'content': 'a'}])
            provider._token_expires_at = 0.0  # имитируем истечение
            provider.complete([{'role': 'user', 'content': 'b'}])

        oauth_calls = [
            call for call in FakeClient.posts if 'oauth' in call['url']
        ]
        self.assertEqual(len(oauth_calls), 2)

    def test_401_invalidates_token_and_retries(self):
        with patch.object(llm_http.httpx, 'post', side_effect=fake_oauth_post), \
                patch.object(llm_http.httpx, 'Client', FakeClient):
            provider = self._provider()
            FakeClient.completions_status = 401
            with self.assertRaises(RuntimeError):
                provider.complete([{'role': 'user', 'content': 'a'}])
            self.assertIsNone(provider._access_token)
            FakeClient.completions_status = 200
            result = provider.complete([{'role': 'user', 'content': 'b'}])
        self.assertEqual(result.text, 'Ответ GigaChat')

    @override_settings(AI_GIGACHAT_AUTH_KEY='key-1', AI_GIGACHAT_MODEL='GigaChat-2-Max')
    def test_factory_returns_gigachat(self):
        provider = get_llm_provider()
        self.assertIsInstance(provider, GigaChatProvider)
        self.assertEqual(provider.authorization_key, 'key-1')
        self.assertEqual(provider.model, 'GigaChat-2-Max')

    @override_settings(AI_GIGACHAT_AUTH_KEY='')
    def test_factory_falls_back_to_stub_without_key(self):
        self.assertIsInstance(get_llm_provider(), StubLLMProvider)

    @override_settings(AI_GIGACHAT_AUTH_KEY='')
    def test_factory_default_stub(self):
        self.assertIsInstance(get_llm_provider(), StubLLMProvider)
