#!/usr/bin/env bash
# backup.sh — бэкап БД PostgreSQL + файлов проекта (СОД).
#
# Запускается из cron:
#   0 2 * * * /root/backup.sh >> /var/log/sod-backup.log 2>&1
#
# Переменные окружения (или задать в .env):
#   PG_HOST      — хост PostgreSQL (по умолчанию 127.0.0.1)
#   PG_PORT      — порт (по умолчанию 5432)
#   PG_USER      — пользователь (по умолчанию admin_db)
#   PG_DB        — база данных (по умолчанию streamcad_db)
#   PG_PASSWORD  — пароль (обязательно)
#   BACKUP_DIR   — директория бэкапов (по умолчанию /root/backups)
#   MEDIA_ROOT   — путь к media-папке проекта (по умолчанию /root/sod-project/media)
#   PROJECT_ROOT — корень проекта (по умолчанию /root/sod-project)

set -euo pipefail

# --- Конфигурация ---
PG_HOST="${PG_HOST:-127.0.0.1}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-admin_db}"
PG_DB="${PG_DB:-streamcad_db}"
PG_PASSWORD="${PG_PASSWORD:?PG_PASSWORD is required}"
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
MEDIA_ROOT="${MEDIA_ROOT:-/root/sod-project/media}"
PROJECT_ROOT="${PROJECT_ROOT:-/root/sod-project}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DB_BACKUP="${BACKUP_DIR}/db_${TIMESTAMP}.backup"
FILES_BACKUP="${BACKUP_DIR}/files_${TIMESTAMP}.tar.gz"

# --- Директория бэкапов ---
mkdir -p "${BACKUP_DIR}"

echo "[$(date)] === Начало бэкапа ==="

# --- 1) PostgreSQL ---
echo "[$(date)] Бэкап БД: ${PG_DB} @ ${PG_HOST}:${PG_PORT}"

DB_SIZE=$(PGPASSWORD="${PG_PASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -t -c \
    "SELECT pg_size_pretty(pg_database_size('${PG_DB}'));" 2>/tmp/pg_dump_err || true)

if [ -s /tmp/pg_dump_err ]; then
    echo "[$(date)] ОШИБКА подключения к БД: $(cat /tmp/pg_dump_err)"
    rm -f /tmp/pg_dump_err
    exit 1
fi

echo "[$(date)] Размер БД: ${DB_SIZE}"

# pg_dump с проверкой: если дамп пустой — ошибка
PGPASSWORD="${PG_PASSWORD}" pg_dump \
    -h "${PG_HOST}" \
    -p "${PG_PORT}" \
    -U "${PG_USER}" \
    --format=custom \
    --compress=6 \
    "${PG_DB}" > "${DB_BACKUP}" 2>/tmp/pg_dump_err

if [ -s /tmp/pg_dump_err ]; then
    echo "[$(date)] ОШИБКА pg_dump: $(cat /tmp/pg_dump_err)"
    rm -f /tmp/pg_dump_err
    rm -f "${DB_BACKUP}"
    exit 1
fi

if [ ! -s "${DB_BACKUP}" ]; then
    echo "[$(date)] ОШИБКА: дамп пустой (0 байт)!"
    exit 1
fi

DB_BACKUP_SIZE=$(du -h "${DB_BACKUP}" | cut -f1)
echo "[$(date)] Дамп создан: ${DB_BACKUP_SIZE}"

# --- 2) Файлы проекта (media + static, без ml_models) ---
echo "[$(date)] Бэкап файлов: ${PROJECT_ROOT}"

if [ -d "${PROJECT_ROOT}" ]; then
    tar czf "${FILES_BACKUP}" \
        --exclude='ml_models' \
        --exclude='*.onnx' \
        --exclude='.cache' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.venv' \
        --exclude='node_modules' \
        --exclude='*.backup' \
        -C "${PROJECT_ROOT}" \
        media static

    if [ -f "${FILES_BACKUP}" ]; then
        FILES_BACKUP_SIZE=$(du -h "${FILES_BACKUP}" | cut -f1)
        echo "[$(date)] Архив создан: ${FILES_BACKUP_SIZE}"
    else
        echo "[$(date)] ВНИМАНИЕ: архив файлов не создан (media/static могут отсутствовать)"
        rm -f "${FILES_BACKUP}"
    fi
else
    echo "[$(date)] ВНИМАНИЕ: ${PROJECT_ROOT} не существует, пропускаем файлы"
fi

# --- 3) Ротация: удаляем бэкапы старше 7 дней ---
echo "[$(date)] Ротация: удаляем бэкапы старше 7 дней"

find "${BACKUP_DIR}" -name 'db_*.backup' -mtime +7 -delete 2>/dev/null || true
find "${BACKUP_DIR}" -name 'files_*.tar.gz' -mtime +7 -delete 2>/dev/null || true

REMAINING_DB=$(find "${BACKUP_DIR}" -name 'db_*.backup' 2>/dev/null | wc -l)
REMAINING_FILES=$(find "${BACKUP_DIR}" -name 'files_*.tar.gz' 2>/dev/null | wc -l)
echo "[$(date)] Осталось дампов: ${REMAINING_DB}, архивов: ${REMAINING_FILES}"

echo "[$(date)] === Бэкап завершён ==="
