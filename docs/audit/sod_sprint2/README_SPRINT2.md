# Спринт 2 — Среда общих данных (Stream-CAD)

Готовый код по роадмапу «Аудита 2.0». Применяется **поверх Спринта 1** (`sod_sprint1.patch`).
Все правки проверены на живом Django-сервере: миграции, smoke-тесты всех экранов,
DOM-проверки вычисленных стилей, VLM-проверка скриншотов (см. `скриншоты/`).

## Состав (6 патчей)

### 2.1. Один источник правды для цветов — `tokens.json`
**Проблема:** три параллельные палитры — CSS-переменные, `STATUS_COLORS` в `pdf_viewer.js`,
`PDF_ISSUE_COLORS` в `services.py`. Правишь цвет в одном месте — два других врут.

**Решение:**
- `static/design-tokens/tokens.json` — единственный файл, где живут цвета
  (вкл. mint-хедер и новые токены поверхностей/границ);
- `scripts/build_design_tokens.py` — генератор: `python scripts/build_design_tokens.py`
  порождает **tokens.css** (CSS-переменные + алиасы `--status-*` + классы `cde-*`),
  **static/js/design-tokens.js** (`window.CDE_TOKENS`) и
  **storage/domain/design_colors.py** (HEX, RGB 0–1 для PyMuPDF, CSS-классы);
- `pdf_viewer.js` больше не хардкодит статусы — читает `window.CDE_TOKENS.statusHex`
  (подключается скриптом перед вьювером);
- `services.py`: `PDF_ISSUE_COLORS` — теперь импорт из `design_colors.py`
  (значения байт-в-байт совпадают со старыми);
- слой алиасов `--status-*` переехал из `viewer.css` в генерируемый `tokens.css`
  (доступен теперь на любой странице);
- `redesign.css` токенизирован: mint-хедер — `var(--c-bg-header)`, ховер строк —
  `var(--c-bg-row-hover)`, акценты — `var(--c-accent)`. Хардкод-хексов в нём больше нет.

### 2.2. CI-проверка дрейфа
- `python scripts/build_design_tokens.py --check` — пересобирает артефакты в памяти
  и падает (exit 1), если кто-то правил сгенерированный файл руками или забыл
  перегенерировать после правки tokens.json;
- `.github/workflows/design-tokens.yml` — гоняет проверку на push/pull_request
  по затронутым путям.

**Правило для команды:** цвет меняется только в `tokens.json` →
`python scripts/build_design_tokens.py` → коммит трёх артефактов.

### 2.3. `base.html` — единый каркас страниц
- `storage/adapters/templates/storage/base.html`: head (viewport, bootstrap,
  **tokens.css теперь на всех страницах**, icons, redesign, app.css), блоки
  `title / body_class / topbar / content / extra_css / extra_js`, bootstrap.bundle всегда;
- мигрированы 6 страниц: project_list, dashboard, edit_member, search, upload, project_detail;
- `document_detail` сознательно оставлен на собственном каркасе — полноэкранный
  вьювер со своим набором стилей (риск регрессии выше пользы);
- бонус-фиксы: bootstrap.bundle теперь грузится и без `can_create_project`
  (раньше модалка создания проекта не открывала остальные модалки),
  `viewport` и токены — везде.

### 2.4. Реестр замечаний проекта
Отвечает на вопрос ГИПа «что требует внимания прямо сейчас» — по всему проекту,
а не по одному документу.

- маршрут `/projects/<id>/issues/` (+ `issues:issue_registry`);
- сводка-чипы с живыми счётчиками: Все / Открытые / В работе / На проверке /
  Закрытые / **Просрочено** — кликабельные фильтры;
- фильтры: статус, ответственный (вкл. «не назначен»), поиск по тексту/шифру/названию;
- строки: №, документ (ссылка во вьювер), статус `cde-badge`, текст, автор,
  ответственный, срок + живой дедлайн-бейдж (тёмплейт-тег Спринта 1), дата;
- **Excel-экспорт** `/projects/<id>/issues/export/` (openpyxl, уже в зависимостях):
  12 колонок, гиперссылки на документы, автофильтр, закреплённая шапка,
  просрочка выделена красным — **экспортируются текущие фильтры**;
- права — как везде: проект через `get_accessible_projects`, документы через
  `get_accessible_documents`;
- вход: кнопка **«Замечания N»** в тулбаре таблицы документов — счётчик красный,
  если есть открытые, и «просроченный» стиль, если горит срок.

### 2.5. Настоящая мини-карта вьювера
Команда снесла старую (4d3620b) — и правильно: это были статичные SVG-заглушки
«01/02». Новая — рабочая:

- живой рендер текущего листа в canvas (масштаб ~168 px);
- синяя рамка видимой области — обновляется при скролле/зуме/повороте
  (rAF, passive listener);
- клик и протяжка по мини-карте перемещают вьюпорт (проверено: скролл 0→106→459);
- плавающая, в правом нижнем углу тёмного канваса — **не отнимает ширину**
  у панели замечаний;
- тумблер в тулбаре + запоминание в localStorage, скрытие < 1000 px;
- мёртвый код старой миникарты удалён (`buildMinimap`, `setMinimapActive`).

### 2.6. Мгновенные фильтры (apply-on-change)
- панель документов: `hx-trigger` чинен — раньше keyup ссылался на несуществующий
  `#quickFilter`; теперь `from:find input[name='q']` (htmx 2.x) — набор текста
  применяется через 350 мс, селекты — по change;
- кнопка «Найти» убрана (скрытый submit оставлен для Enter/скринридеров);
- реестр замечаний — тот же паттерн.

## Как применить

```bash
# вариант А: файлами (каталог «файлы/» повторяет структуру репо)
cp -r файлы/* /path/to/sod-project/

# вариант Б: патчем (требует применённого Спринта 1)
git apply --check sod_sprint2.patch   # сухой прогон
git apply sod_sprint2.patch

# если Спринт 1 ещё не применялся — кумулятивный патч
git apply sod_sprint1+2_cumulative.patch
```

Новых зависимостей нет (openpyxl уже в requirements.txt). Миграций БД нет.

## Как проверить после применения

```bash
python manage.py check                      # 0 issues
python scripts/build_design_tokens.py --check   # OK: синхронно
python manage.py runserver
# 1. Проекты → проект → кнопка «Замечания N» в тулбаре
# 2. Реестр: чипы, фильтры, срок-бейджи, «Excel»
# 3. Вьювер: мини-карта справа внизу, зум 200% → рамка меньше листа,
#    клик по мини-карте перемещает вьюпорт, тумблер в тулбаре
# 4. Таблица документов: смените «Статус» — обновится без кнопки «Найти»
```

## Что сознательно НЕ тронуто
- `app.css` (2447 строк, `--app-*`) — отдельный рефакторинг, нужен поэтапный перенос
  на токены (кандидат в Спринт 3);
- `document_detail.html` не мигрирован на base.html (см. 2.3);
- IssueTable в `issues/tables.py` — мёртвый код (нигде не импортируется), не удалён:
  возможно, команда планировала его использовать.

## Файлы
| Файл | Статус |
|---|---|
| `static/design-tokens/tokens.json` | новый/обновлённый источник правды |
| `scripts/build_design_tokens.py` | новый генератор + `--check` |
| `.github/workflows/design-tokens.yml` | новый CI |
| `static/design-tokens/tokens.css` | генерируемый |
| `static/js/design-tokens.js` | генерируемый |
| `storage/domain/design_colors.py` | генерируемый |
| `static/js/pdf_viewer.js` | цвета из токенов + мини-карта |
| `storage/domain/services.py` | PDF_ISSUE_COLORS из токенов |
| `storage/adapters/templates/storage/base.html` | новый каркас |
| 6 страниц + `_documents_panel.html` | миграция на base / мгновенные фильтры |
| `issues/views.py`, `issues/urls.py` | реестр + Excel |
| `issues/templates/issues/registry.html` | новый экран |
| `issues/templatetags/issue_extras.py` | + фильтр `status_badge_class` |
| `storage/adapters/views.py` | + счётчики замечаний в контекст |
| `static/css/viewer.css`, `static/css/redesign.css` | алиасы → tokens.css, токенизация |
