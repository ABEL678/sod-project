#!/usr/bin/env bash
# Протокол приёмки патча stream-cad_pymupdf-to-pypdfium2.patch
# (см. README_APPLY.md, раздел 5). Запускать ИЗ КОРНЯ репозитория.
# Базовый коммит: cdb88c1 (ветка main).
set -euo pipefail
PATCH="$(cd "$(dirname "$0")/.." && pwd)/stream-cad_pymupdf-to-pypdfium2.patch"

echo "[1/5] Текущий коммит: $(git rev-parse --short=7 HEAD) (патч рассчитан на cdb88c1)"
git apply --check "$PATCH"
echo "[2/5] git apply --check — конфликтов нет"
git apply "$PATCH"
echo "[3/5] Патч применён (17 файлов)"

pip uninstall -y pymupdf pymupdf4llm pymupdf-layout >/dev/null 2>&1 || true
echo "[4/5] AGPL-пакеты удалены; установка зависимостей из requirements.txt..."
pip install -r requirements.txt

echo "[5/5] Тестовый прогон (ожидается: OK, PyMuPDF не установлен)..."
python manage.py test

echo ""
echo "ПРИЁМКА ПРОЙДЕНА. Далее: python manage.py create_demo_project"
