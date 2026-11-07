# Применение патча stream-cad_pymupdf-to-pypdfium2.patch

**Базовый коммит:** `cdb88c1` (ветка `main`, «feat(ai_checks): выбор LLM-модели, RAG-синтез, UX-фиксы»)
**Состав:** 17 файлов (15 изменённых, 2 новых), +812/−285 строк.
**Эталонный коммит в рабочем клоне аудита:** `b103fe7`.

## 1. Что делает патч

Убирает зависимость от PyMuPDF (AGPL-3.0 / коммерческая лицензия Artifex)
и заменяет её на полностью перmissive-стек:

| Библиотека | Лицензия | Роль |
| --- | --- | --- |
| pypdfium2 5.7.0 | BSD-3-Clause / Apache-2.0 (pdfium — BSD, Chromium) | разбор/рендер/текст/геометрия PDF |
| reportlab 4.4.9 | BSD-3-Clause | генерация оверлеев и demo-PDF |
| pypdf 6.6.0 | BSD-3-Clause | слияние оверлея с исходным PDF |

## 2. Применение (HANDOFF №8)

```bash
# из корня репозитория на коммите cdb88c1
git apply --check stream-cad_pymupdf-to-pypdfium2.patch   # проверка
git apply stream-cad_pymupdf-to-pypdfium2.patch           # применение
```

Патч **не содержит** миграций, изменений бизнес-логики и внешних файлов.

## 3. Зависимости

```bash
pip uninstall pymupdf pymupdf4llm pymupdf-layout   # AGPL-пакеты и неиспользуемые
pip install -r requirements.txt                    # pypdfium2, reportlab, pypdf
```

`pymupdf4llm` и `pymupdf_layout` были в requirements, но нигде не
импортировались (проверено поиском по коду на cdb88c1) — удалены.

## 4. Верификация (выполнена аудиторской средой, Linux, Python 3.12)

1. `git apply --check` на свежем клоне `cdb88c1` — без конфликтов.
2. После применения — байт-идентичность эталонному состоянию (17/17 файлов).
3. Полный тестовый прогон **без установленного PyMuPDF**:
   `python manage.py test` → **529 tests, OK** (1 skip — pgvector-тест,
   требует PostgreSQL, не связан с заменой).
4. Дифференциальные прогоны против PyMuPDF 1.26.7:
   - правила СПДС (рамка/штамп/шрифт) — находки идентичны на 7 сценариях;
   - рендер кропа — 100% совпадение пикселей (rot 0/90/180/270,
     сдвинутый MediaBox);
   - экспорт PDF с маркапами — 100% совпадение позиций маркапов
     (rot 0/90/180/270).

## 5. Протокол приёмки у владельца

```bash
git apply --check stream-cad_pymupdf-to-pypdfium2.patch
git apply stream-cad_pymupdf-to-pypdfium2.patch
pip uninstall pymupdf pymupdf4llm pymupdf-layout
pip install -r requirements.txt
python manage.py test          # ожидается: OK
python manage.py create_demo_project   # demo-PDF через reportlab
```
