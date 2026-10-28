# Деплой СОД на Ubuntu VPS

Плейсхолдеры в конфигурациях: `example.com`, `/opt/sod`, `soduser`.
Замените их на реальные домен, путь и пользователя.

## 0. Требования

- Ubuntu 22.04/24.04, домен с A-записью на IP сервера.
- Python 3.12+ (проект проверен на 3.14; на Ubuntu 24.04 доступен `python3.12`).
- PostgreSQL 14+.
- Доступ `sudo`.

## 1. Пакеты

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx postgresql postgresql-contrib certbot
sudo systemctl enable --now nginx postgresql
```

## 2. Пользователь и каталог

```bash
sudo adduser --disabled-password --gecos "" soduser
sudo usermod -aG www-data soduser
sudo mkdir -p /opt/sod
sudo chown soduser:www-data /opt/sod
```

## 3. Код проекта

```bash
sudo -u soduser git clone <REPO_URL> /opt/sod
sudo chown -R soduser:www-data /opt/sod
sudo chmod -R u+rwX,g+rX /opt/sod
```

Если каталог уже заполнен — используйте `rsync` вместо `git clone`.

## 4. Виртуальное окружение

```bash
sudo -u soduser python3 -m venv /opt/sod/.venv
sudo -u soduser /opt/sod/.venv/bin/pip install --upgrade pip
sudo -u soduser /opt/sod/.venv/bin/pip install -r /opt/sod/deploy/requirements-production.txt
```

`deploy/requirements-production.txt` добавляет `gunicorn` и `psycopg2-binary`,
не меняя локальный `requirements.txt`.

## 5. Переменные окружения

```bash
sudo -u soduser cp /opt/sod/.env.production.example /opt/sod/.env
sudo -u soduser chmod 600 /opt/sod/.env
sudo -u soduser nano /opt/sod/.env
```

Сгенерировать `DJANGO_SECRET_KEY`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Заполните домен, пароль PostgreSQL и `DJANGO_SECRET_KEY`. Пароль БД используйте
без пробелов, `$` и обратных кавычек, чтобы `.env` одинаково читался systemd и shell.
Реальный `.env` не коммитится.

## 6. PostgreSQL

```bash
sudo -u postgres psql
```

```sql
CREATE USER sod WITH PASSWORD 'replace-with-a-strong-password';
CREATE DATABASE sod OWNER sod;
ALTER ROLE sod SET client_encoding TO 'utf8';
ALTER ROLE sod SET default_transaction_isolation TO 'read committed';
ALTER ROLE sod SET timezone TO 'Europe/Moscow';
\q
```

Проверка подключения:

```bash
PGPASSWORD='replace-with-a-strong-password' psql -h 127.0.0.1 -U sod -d sod -c 'select 1;'
```

## 7. Миграции и статика

```bash
sudo -u soduser bash -c 'set -a; . /opt/sod/.env; set +a; cd /opt/sod && .venv/bin/python manage.py migrate'
sudo -u soduser bash -c 'set -a; . /opt/sod/.env; set +a; cd /opt/sod && .venv/bin/python manage.py collectstatic --noinput'
sudo -u soduser bash -c 'set -a; . /opt/sod/.env; set +a; cd /opt/sod && .venv/bin/python manage.py createsuperuser'
```

## 8. Gunicorn через systemd

```bash
sudo cp /opt/sod/deploy/gunicorn.service /etc/systemd/system/gunicorn.service
sudo systemctl daemon-reload
sudo systemctl enable --now gunicorn
sudo systemctl status gunicorn
sudo journalctl -u gunicorn -n 50 --no-pager
```

Проверить сокет:

```bash
ls -l /run/gunicorn/sod.sock
```

## 9. Nginx: bootstrap и сертификат

Сначала HTTP-конфиг для ACME:

```bash
sudo mkdir -p /var/www/certbot
sudo cp /opt/sod/deploy/nginx-bootstrap.conf /etc/nginx/sites-available/sod
sudo ln -sf /etc/nginx/sites-available/sod /etc/nginx/sites-enabled/sod
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Убедиться, что домен указывает на сервер:

```bash
dig +short example.com
```

Получить сертификат:

```bash
sudo certbot certonly --webroot -w /var/www/certbot \
  -d example.com -d www.example.com \
  --agree-tos -m admin@example.com --no-eff-email
```

Установить финальный HTTPS-конфиг:

```bash
sudo cp /opt/sod/deploy/nginx.conf /etc/nginx/sites-available/sod
sudo nginx -t
sudo systemctl reload nginx
```

Проверка:

```bash
curl -I http://example.com     # ожидается 301 на https
curl -I https://example.com    # ожидается 200
sudo certbot renew --dry-run
```

## 10. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 11. Проверка, что медиа недоступно напрямую

Публичный `/media/` закрыт через `internal`. Проверьте это снаружи:

```bash
# 1. Посмотрите, какие файлы лежат в media
find /opt/sod/media -type f | head

# 2. Попробуйте открыть файл по прямому URL (подставьте реальный путь)
curl -I https://example.com/media/documents/2026/10/example.pdf

# 3. Проверьте корень media
curl -I https://example.com/media/
```

Ожидаемый результат: `404 Not Found` или `403 Forbidden`, но не `200 OK`
и не содержимое файла.

Штатное скачивание идёт через Django:

```bash
# Без авторизации — редирект на /login/
curl -I https://example.com/download/1/
```

С авторизованной сессией файл отдаёт `DocumentDownloadView` после проверки прав.

## 12. Обновление приложения

```bash
sudo -u soduser git -C /opt/sod pull
sudo -u soduser /opt/sod/.venv/bin/pip install -r /opt/sod/deploy/requirements-production.txt
sudo -u soduser bash -c 'set -a; . /opt/sod/.env; set +a; cd /opt/sod && .venv/bin/python manage.py migrate'
sudo -u soduser bash -c 'set -a; . /opt/sod/.env; set +a; cd /opt/sod && .venv/bin/python manage.py collectstatic --noinput'
sudo systemctl restart gunicorn
```

## 13. Диагностика

```bash
sudo systemctl status gunicorn
sudo journalctl -u gunicorn -f
sudo nginx -t
sudo tail -n 50 /var/log/nginx/error.log
ls -l /run/gunicorn/sod.sock
```

Если Gunicorn не стартует — проверьте `/opt/sod/.env`, права на сокет и `journalctl`.

## Безопасность медиа

Публичный `/media/` отключён (`internal`). Документы отдаются через
`/download/<version_id>/` после `check_read_permission()`. Не убирайте
`internal` без отдельной проверки прав.

## Проверка production-настроек

```bash
sudo -u soduser bash -c 'set -a; . /opt/sod/.env; set +a; cd /opt/sod && .venv/bin/python manage.py check --deploy'
```

Предупреждение Django про HSTS можно игнорировать: HSTS выставляется Nginx.
Остальные предупреждения (`SECRET_KEY`, `DEBUG`, SSL redirect) должны исчезнуть
после заполнения `.env`.
