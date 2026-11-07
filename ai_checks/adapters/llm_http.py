"""HTTP-адаптер LLMProvider (Этап 19, Ф4) + заглушка без API-ключа.

Провайдер — OpenAI-совместимый chat/completions (DeepSeek по умолчанию).
Тексты промптов/ответов в БД не пишутся (приватность) — только метаданные
в LLMCallLog. Retry ×3, таймаут 60 с (ARCHITECTURE.md 5.3).
"""

from __future__ import annotations

import time

import httpx
from django.conf import settings

from ai_checks.ports.llm import LLMResponse


class HttpLLMProvider:
    """OpenAI-совместимый HTTP-провайдер (DeepSeek/OpenRouter)."""

    name = 'deepseek'

    def __init__(
        self,
        api_key: str,
        base_url: str = 'https://api.deepseek.com/v1',
        model: str = 'deepseek-chat',
        timeout: float = 60.0,
        retries: int = 3,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout = timeout
        self.retries = retries

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
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        f'{self.base_url}/chat/completions',
                        headers={
                            'Authorization': f'Bearer {self.api_key}',
                            'Content-Type': 'application/json',
                        },
                        json=payload,
                    )
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
        raise RuntimeError(f'LLM-запрос не удался: {last_error}')


class StubLLMProvider:
    """Заглушка без API-ключа: детерминированное резюме из контекста."""

    name = 'stub'

    def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        context = ''
        for message in reversed(messages):
            if message.get('role') == 'user':
                context = str(message.get('content') or '')
                break
        summary = 'Сводка без LLM (ключ не задан).\n' + context[:1200]
        return LLMResponse(
            text=summary,
            model='stub',
            prompt_tokens=sum(
                len(str(m.get('content') or '')) for m in messages
            ),
            completion_tokens=len(summary),
            latency_ms=0,
        )


def get_llm_provider():
    """Фабрика: HTTP-провайдер при заданном ключе, иначе — заглушка."""
    api_key = (getattr(settings, 'AI_LLM_API_KEY', '') or '').strip()
    if not api_key:
        return StubLLMProvider()
    return HttpLLMProvider(
        api_key=api_key,
        base_url=getattr(
            settings, 'AI_LLM_BASE_URL', 'https://api.deepseek.com/v1',
        ),
        model=getattr(settings, 'AI_LLM_MODEL', 'deepseek-chat'),
        timeout=float(getattr(settings, 'AI_LLM_TIMEOUT', 60)),
    )
