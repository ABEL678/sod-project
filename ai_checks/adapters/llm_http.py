"""HTTP-адаптеры LLMProvider (Этап 19, Ф4) + заглушка без API-ключа.

Рабочий провайдер один (зачистка 2026-11-10, «только экосистема Сбера»):
- GigaChat (Сбер, работа из РФ) — OAuth + OpenAI-совместимый API;
  ключ — AI_GIGACHAT_AUTH_KEY (Authorization Key, Base64).
OrcaRouter и прочие сторонние шлюзы удалены вместе с настройками
AI_LLM_* — любая модель из AI_AVAILABLE_MODELS уходит в GigaChatProvider.
Эмбеддинги того же контура — ai_checks/adapters/embedding_gigachat.py.

Тексты промптов/ответов в БД не пишутся (приватность) — только
метаданные в LLMCallLog. Retry ×2, таймаут ≤ 12 с (ARCHITECTURE.md 5.3,
бюджет синхронного вызова < 30 с воркера gunicorn).
"""

from __future__ import annotations

import threading
import time
import uuid

import httpx
from django.conf import settings

from ai_checks.ports.llm import LLMResponse


class StubLLMProvider:
    """Заглушка без API-ключа: формирует связный ответ из контекста.

    Когда LLM недоступен, сервис форматирует найденные чанки в читаемый
    ответ с указанием источников — не просто склейку, а структурированный
    текст, который пользователь поймёт.
    """

    name = 'stub'

    def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        # Извлекаем вопрос и контекст из сообщений.
        question = ''
        context_text = ''
        for message in messages:
            role = message.get('role', '')
            content = str(message.get('content') or '')
            if role == 'user':
                # Вопрос идёт первым, затем контекст.
                if 'Вопрос:' in content:
                    parts = content.split('Вопрос:', 1)
                    if 'Фрагменты документов:' in parts[1]:
                        q, ctx = parts[1].split('Фрагменты документов:', 1)
                        question = q.strip()
                        context_text = ctx.strip()
                    else:
                        question = parts[1].strip()
                elif not question:
                    question = content
            elif role == 'system':
                # Системное сообщение игнорируем для извлечения данных.
                pass

        # Формируем связный ответ из фрагментов.
        if context_text:
            # Парсим фрагменты [N] title:\ntext
            fragments = []
            current = None
            for line in context_text.split('\n'):
                if line.startswith('[') and '] ' in line and ':' in line:
                    if current:
                        fragments.append(current)
                    current = line
                elif current is not None:
                    current += '\n' + line
            if current:
                fragments.append(current)

            # Формируем читаемый ответ.
            answer_lines = [
                f'Ответ на вопрос: {question}',
                '',
                'На основе найденных документов:',
            ]
            for i, frag in enumerate(fragments[:3], 1):
                # Извлекаем заголовок и текст фрагмента.
                if '\n' in frag:
                    title, text = frag.split('\n', 1)
                    answer_lines.append(f'{i}. {title}:')
                    answer_lines.append(text[:500])
                    answer_lines.append('')
                else:
                    answer_lines.append(f'{i}. {frag[:500]}')
                    answer_lines.append('')

            answer = '\n'.join(answer_lines)
        else:
            answer = 'Ответ на вопрос: ' + question + '\n\nИнформация не найдена.'

        total_tokens = sum(
            len(str(m.get('content') or '')) for m in messages
        )
        return LLMResponse(
            text=answer,
            model='stub',
            prompt_tokens=total_tokens,
            completion_tokens=len(answer),
            latency_ms=0,
        )


def get_llm_provider(model: str | None = None):
    """Фабрика провайдера (зачистка 2026-11-10: только GigaChat).

    Любая модель из AI_AVAILABLE_MODELS уходит в GigaChatProvider
    (Сбер, РФ); model=None → модель из AI_GIGACHAT_MODEL.
    Нет ключа AI_GIGACHAT_AUTH_KEY → заглушка StubLLMProvider
    (контур не падает, ответ синтезируется из найденных фрагментов).
    """
    auth_key = (
        getattr(settings, 'AI_GIGACHAT_AUTH_KEY', '') or ''
    ).strip()
    if not auth_key:
        return StubLLMProvider()
    # Бюджет синхронного LLM-вызова: 12 c x 2 попытки + паузы ~ 1.5 c
    # = максимум ~25.5 c < 30 c (таймаут воркера gunicorn).
    return GigaChatProvider(
        auth_key,
        model=model or getattr(
            settings, 'AI_GIGACHAT_MODEL', 'GigaChat-2-Max',
        ),
        timeout=min(
            float(getattr(settings, 'AI_GIGACHAT_TIMEOUT', 60)), 12.0,
        ),
        verify_ssl=bool(
            getattr(settings, 'AI_GIGACHAT_VERIFY_SSL', True),
        ),
        retries=2,
    )


class GigaChatProvider:
    """GigaChat (Сбер) из РФ: Authorization Key → OAuth-токен → API.

    Двухэтапная авторизация: POST на OAuth-эндпоинт с Authorization Key
    (Base64) и RqUID, затем OpenAI-совместимый chat/completions с
    Bearer-токеном. Токен живёт ~30 мин — кэшируем в памяти 25 мин
    (в БД не храним). Документация:
    https://developers.sber.ru/docs/ru/gigachat/guides/compatible-openai
    """

    name = 'gigachat'
    OAUTH_URL = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'
    API_BASE_URL = 'https://api.giga.chat/v1'
    TOKEN_TTL_SECONDS = 25 * 60

    def __init__(
        self,
        authorization_key: str,
        model: str = 'GigaChat-2-Max',
        timeout: float = 60.0,
        retries: int = 3,
        verify_ssl: bool = True,
    ) -> None:
        self.authorization_key = authorization_key
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.verify_ssl = verify_ssl
        # Базовый URL OpenAI-совместимого API: AI_GIGACHAT_API_BASE_URL,
        # по умолчанию — публичный контур api.giga.chat/v1.
        self.api_base_url = (
            getattr(settings, 'AI_GIGACHAT_API_BASE_URL', '')
            or self.API_BASE_URL
        ).rstrip('/')
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = threading.Lock()

    def get_token(self) -> str:
        """OAuth-токен: из кэша (25 мин) или новый запрос.

        Публичный метод: используется и эмбеддингами GigaChat
        (embedding_gigachat.py) — ключ авторизации один на контур.
        """
        with self._token_lock:
            now = time.monotonic()
            if self._access_token and now < self._token_expires_at:
                return self._access_token

            response = httpx.post(
                self.OAUTH_URL,
                headers={
                    'Authorization': f'Bearer {self.authorization_key}',
                    'RqUID': str(uuid.uuid4()),
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                data={'scope': 'GIGACHAT_API_PERS'},
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            token = response.json()['access_token']
            self._access_token = token
            self._token_expires_at = now + self.TOKEN_TTL_SECONDS
            return token

    def invalidate_token(self) -> None:
        with self._token_lock:
            self._access_token = None
            self._token_expires_at = 0.0

    def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        payload = {
            'model': model or self.model,
            'messages': messages,
        }
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}

        last_error: Exception | None = None
        for attempt in range(self.retries):
            started = time.monotonic()
            try:
                token = self.get_token()
                with httpx.Client(
                    timeout=self.timeout, verify=self.verify_ssl,
                ) as client:
                    response = client.post(
                        f'{self.api_base_url}/chat/completions',
                        headers={
                            'Authorization': f'Bearer {token}',
                            'Content-Type': 'application/json',
                        },
                        json=payload,
                    )
                    if response.status_code == 401:
                        # Токен отозван/истёк раньше — сбрасываем кэш.
                        self.invalidate_token()
                    response.raise_for_status()
                    data = response.json()
                latency_ms = int((time.monotonic() - started) * 1000)
                content = data['choices'][0]['message']['content']
                usage = data.get('usage') or {}
                return LLMResponse(
                    text=content,
                    model=data.get('model') or payload['model'],
                    prompt_tokens=int(usage.get('prompt_tokens') or 0),
                    completion_tokens=int(
                        usage.get('completion_tokens') or 0
                    ),
                    latency_ms=latency_ms,
                    raw={'id': data.get('id')},
                )
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f'GigaChat-запрос не удался: {last_error}')
