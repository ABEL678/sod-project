# СОД для проектной компании

Внутренняя система общих данных (СОД) для небольшой проектной компании (25 человек).

## Документация
- [Архитектура](docs/ARCHITECTURE.md) — как устроена система, модели данных, правила для разработки.
- [Мастер-спека](docs/MASTER_SPEC.md) — что делаем, этапы и контрольные точки.

## Стек
- Python 3.14
- Django
- SQLite (на старте), PostgreSQL (на сервере через переменные окружения)
- ИИ-агент Kilo (PyCharm) для разработки

## Переменные окружения
Настройки читаются из окружения (см. `.env.example`). Файл `.env` автоматически не загружается:
задайте переменные в PyCharm Run Configuration или в окружении сервера.

- `DJANGO_SECRET_KEY` — секретный ключ (обязателен в production).
- `DJANGO_DEBUG` — `True` для локальной разработки, по умолчанию `False`.
- `DJANGO_ALLOWED_HOSTS` — список хостов через запятую.
- `DJANGO_CSRF_TRUSTED_ORIGINS` — доверенные origins для HTTPS.
- `DJANGO_SECURE_COOKIES`, `DJANGO_SECURE_SSL_REDIRECT` — HTTPS-защита.
- `POSTGRES_DB` (и `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_HOST`/`POSTGRES_PORT`) —
  если задан `POSTGRES_DB`, используется PostgreSQL, иначе SQLite. На сервере
  дополнительно установите драйвер: `pip install "psycopg[binary]"` (в `requirements.txt`
  он сознательно не включён, чтобы не ломать локальную установку).

Для локального запуска с отладочной статикой:
`DJANGO_DEBUG=True` + `.venv\Scripts\python.exe manage.py runserver`.