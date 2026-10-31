# СОД Stream-CAD · Дизайн-токены v1.0

Единый источник правды по цвету, типографике и статусам. Закрывает главный
находка аудита — рассинхрон цветов статусов между маркапами на PDF
(`STATUS_COLORS` в `pdf_viewer.js`) и бейджами карточки (`bg-*` в `_card.html`).

## Состав

| Файл | Назначение |
|---|---|
| `tokens.json` | Источник правды. Отсюда генерируется всё остальное |
| `tokens.css` | CSS-переменные (+ заготовка тёмной темы), подключить после `bootstrap.min.css` |
| `tailwind.config.js` | Маппинг токенов для будущих проектов экосистемы (ГИП-Ассистент и др.) |
| `bootstrap-override.scss` | Мост на переменные Bootstrap 5 + мягкие бейджи `.badge-status-*` |
| `preview.html` | Живой шоукейс: палитра, типографика, бейджи, маркапы, дедлайны |
| `README.md` | Этот файл |

## Карта статусов (Issue.STATUS_CHOICES → токен)

| Код модели | Текущая надпись | Токен | Цвет | Фон 10–12% |
|---|---|---|---|---|
| `NEW` | Новое | `--s-wait-solid` | `#0d6efd` | `#E7F0FF` |
| `IN_PROGRESS` | Принято в работу | `--s-progress-solid` | `#0dcaf0` | `#E0F8FC` |
| `DISCUSSION` | К обсуждению | `--s-review-solid` | `#ffc107` | `#FFF8E1` |
| `DONE` | Выполнено | `--s-done-solid` | `#198754` | `#E6F4EE` |
| `CLOSED` | Закрыто | `--s-closed-solid` | `#6c757d` | `#F0F1F2` |
| `REJECTED` | Не принято | `--s-attention-solid` | `#dc3545` | `#FDEBED` |
| `CANCELLED` | Аннулировано | `--s-canceled-solid` | `#212529` | `#E9EAEA` |

Правила:
1. **Один статус = один цвет** в панели списка, бейдже карточки, маркапе на листе
   и в PDF-экспорте PyMuPDF.
2. **REJECTED и CANCELLED — результаты, а не состояния.** Красным подсвечиваются
   также просроченные дедлайны любого статуса.
3. Коды модели менять не нужно — миграций нет, меняется только визуальный слой.

## Как внедрить (волна 1, без миграций)

1. **CSS.** Скопировать `tokens.css` в `static/css/`, подключить после Bootstrap:
   ```html
   <link href="{% static 'css/bootstrap.min.css' %}" rel="stylesheet">
   <link href="{% static 'css/tokens.css' %}" rel="stylesheet">
   ```
2. **JS.** Заменить `STATUS_COLORS` в `static/js/pdf_viewer.js` на чтение из
   CSS-переменных (или сгенерировать `status_map.js` из `tokens.json`):
   ```js
   const STATUS_TOKENS = {
     NEW: 'wait', IN_PROGRESS: 'progress', DISCUSSION: 'review',
     DONE: 'done', CLOSED: 'closed', REJECTED: 'attention', CANCELLED: 'canceled',
   };
   function statusColor(status) {
     return getComputedStyle(document.documentElement)
       .getPropertyValue('--status-' + STATUS_TOKENS[status]).trim();
   }
   ```
3. **Шаблоны.** В `issues/templates/issues/_card.html` заменить ветку `bg-*`
   на `.badge badge-status-{{ token }}` (маппинг из таблицы выше).
4. **Экспорт.** Сгенерировать `status_palette.py` из `tokens.json` и отдать его
   рендеру маркапов в PyMuPDF — бумажный экспорт получит те же цвета.

## Генерация производных файлов

`tokens.json` — единственное место, где меняются цвета. Раз в правку:

- `tokens.css` — руками или простым скриптом (структура зеркальная);
- `status_map.js` / `status_palette.py` — 20-строчный генератор на Python
  (`json.load` → шаблоны), достаточно писать при изменении токенов.

## Проверка

Откройте `preview.html` — там живой шоукейс всех токенов: палитра, шкала
типографики, бейджи всех статусов, примеры маркапов и живой бейдж дедлайна
(считает дни от текущей даты).
