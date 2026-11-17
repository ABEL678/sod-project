# План: подсветка пина замечания на PDF при переходе из вкладки «Замечания» («issue-pin-highlight»)

**Роль:** Аудитор (проект Stream-CAD, репозиторий `ABEL678/sod-project`).
**База:** `main`, HEAD = **77fe00b** (S3-синхронизация, Этапы A–D закрыты; вкладка «Замечания» — коммиты 039c580 / cdc5e37 / b6f361a).
**Статус:** проект плана — только проектирование. Код не пишется, коммиты не делаются.
**Терминология:** «Сводка», «вкладка «Замечания»» — как в ISSUES_TAB_PLAN.md; никаких «дашбордов».

---

## 1. Резюме (TL;DR)

| Вопрос задачи | Ответ/решение |
|---|---|
| 1. Как рендерятся пины на canvas | Не на canvas, а в DOM-слое `.pdf-pin-layer` поверх `<canvas>`: `drawMarksInto()` строит `div.pdf-markup[data-issue-id]` из `x_ratio/y_ratio` (+ прямоугольник `x1..y2_ratio`) в каноническом пространстве листа с пересчётом на поворот. Перерисовка — при каждом `renderPage()` (см. §2.1) |
| 2. Есть ли функция подсветки и почему не срабатывает | Есть **три** механизма, и все три при переходе из вкладки не работают: (а) внутренняя `selectIssue()` pdf_viewer.js — умирает на early-return, т.к. в `document_detail.html` нет `#issueDetails`; (б) `window.centerMarkup()` (sync.js) вешает `.border-danger` — вызывается только по клику по карточке, а из deep-link цепочка ломается; (в) персистентная перерисовка `is-active` по `selectedIssueId` (drawMarksInto:374-381) — не активируется, потому что `selectedIssueId` на этой странице никто не выставляет. Главная причина: **в ссылках вкладки нет якоря `#issue-N`** — на HEAD 77fe00b оба href содержат только `?page=N` (grep подтверждает: ни один шаблон не генерирует `#issue-`) |
| 3. Решение | Новая публичная функция **`window.focusIssue(issueId)`** внутри pdf_viewer.js — единая точка подсветки: выставляет `selectedIssueId`, перерисовывает пины (персистентная красная рамка выживает re-render), вешает транзиентный пульс `.is-flash`, центрирует лист на пине, корректно обрабатывает кластеры. Правая панель и строки подсвечиваются существующими `openIssueCard`/`table-active` |
| 4. Изменения JS/CSS | 5 файлов: `pdf_viewer.js` (focusIssue + dataset.issueIds у кластеров), `sync.js` (selectIssue/selectIssueFromPdf → focusIssue), `document_detail.html` (`_goToPageThenHighlight` v2), `_issues_panel.html` (якоря `#issue-{{ pk }}`), `app.css` (`.is-flash`-пульс, красный `.is-active`, reduced-motion) |

Ключевой принцип: **подсветка пина — состояние вьювера, а не разовый класс в DOM**. Любая перерисовка листа (zoom, поворот, смена статуса) обязана восстанавливать подсветку — для этого уже есть механизм `selectedIssueId` (drawMarksInto:374-381), его нужно «оживить», а не городить второй.

---

## 2. Диагностика с пруфами

### 2.1. Как устроены пины сегодня

| Якорь | Где | Что делает |
|---|---|---|
| Данные замечаний | `document_detail.html:495` (`{{ issues_json\|json_script:"issues-data" }}`) → `pdf_viewer.js:56-57` | Массив issues с `id, page_number, x_ratio, y_ratio, x1..y2_ratio, status, description` читается вьювером при инициализации |
| Координаты | `issues/models.py:54-60` + CheckConstraint `issue_pin_coords_consistency` (`:85-93`) | Пин есть ⟺ задан `page_number`; тогда `x_ratio/y_ratio` обязаны быть заданы (констрейнт). Прямоугольник — опционален |
| Каноническое пространство | `pdf_viewer.js:257-298` (`toDisplayPoint/toDisplayRect`) | Координаты хранятся для неповёрнутого листа; на отрисовке пересчитываются под текущий `currentRotation` |
| Отрисовка | `pdf_viewer.js:300-382` `drawMarksInto(pageMap, pageNumber)` | Очищает слой (`:305-308`), собирает items по `page_number` (`:313-356`), кластеризует ближе 28 px (`:358-363`, `CLUSTER_THRESHOLD_PX` `:384`), рисует |
| Одиночный пин | `pdf_viewer.js:431-489` `drawMarkupFrame` | `div.pdf-markup` c `frame.dataset.issueId` (`:436`), позиция/размер в % (`:437-440`), **inline** `borderColor = statusColor(status)` (`:441`), круглый бейдж `pdf-issue-badge` c номером (`:459-488`), клик → `window.selectIssueFromPdf` (`:446-451`) |
| Кластер | `pdf_viewer.js:491-530` `drawClusterBadge` | `button.pdf-pin-cluster` — **не имеет `data-issue-id`**; клик → зум или попап со списком (`openClusterPopup` `:543-586`) |
| Жизненный цикл | `pdf_viewer.js:901-959` `renderPage` | Вьювер одностраничный: **удаляет все `.pdf-page` и очищает `pages`** при каждом рендере (`:914-917`); пины рисуются в хвосте promise рендера (`:951-957` `drawMarks(currentPage)`) |
| Вход в подсветку извне | `pdf_viewer.js:2463-2474` `window.goToPage` | Возвращает promise рендера (**или `undefined`, если `!pdfDocument`** — `renderPage:902-904`); `window.getCurrentPage` `:2782-2784` |
| Guard актуальности | `pdf_viewer.js:53` `isCurrentView` + `drawMarks:250-255` | Для неактуальных версий пины вообще не рисуются |

Слой пинов — `app.css:2289-2293` `.pdf-pin-layer` (absolute, `pointer-events: none`), сами пины `pointer-events: auto` (js `:443`).

### 2.2. Механизмы выделения, которые уже есть

| Механизм | Где | Состояние на `document_detail.html` |
|---|---|---|
| Внутренняя `selectIssue(issueId, options)` | `pdf_viewer.js:145-238` | **Мёртв.** Early-return `:147-149` (`if (!issue || !details)`), где `details = getElementById('issueDetails')` (`:72`) — **такого id в шаблоне нет** (шаблон использует свою панель `#detail`/`dNo`/`dBadge`/`detailContent`, `document_detail.html:479-490`). Функция рассчитана на старую панель деталей (`issueElement`, `detailId`, …) |
| Персистентная подсветка по `selectedIssueId` | `pdf_viewer.js:104`, ре-apply в `drawMarksInto:374-381` (класс `.is-active` после каждой перерисовки) | **Не активируется** — `selectedIssueId` выставляется только мёртвой `selectIssue`; на этой странице остаётся `null` навсегда |
| `window.selectIssue(event, issueId)` (sync.js) | `sync.js:27-69` | Живой обработчик клика по карточке (`document_detail.html:421` `onclick="selectIssue(null, {{ issue.pk }}"`) и вызывается из `openIssueCard` (`:744-746`). Делает: `table-active` строке, при необходимости `goToPage`, затем `centerMarkup` |
| `window.centerMarkup(issueId)` (sync.js) | `sync.js:1-25` | Разовая подсветка: снимает `.border-danger` со всех пинов, вешает на целевой `.pdf-markup[data-issue-id]`, скроллит `.stage` к центру пина. CSS: `app.css:2475-2479` (красная тень+outline) |
| `window.selectIssueFromPdf(issueId)` (sync.js) | `sync.js:71-90` | Клик по пину: раскрыть список, подсветить строку, `centerMarkup` |
| Карточка в правой панели | `document_detail.html:739-779` `openIssueCard`; CSS `viewer.css:172-175` `.panel .card.is-active` | Работает: открывает `#detail`, грузит HTMX-контент, вешает `is-active` карточке |
| Стили активного пина | `app.css:2364-2366` `.pdf-markup.is-active` (голубое свечение `--c-accent`), `:2313-2317` `.pdf-pin.is-active` (legacy) | Классы существуют, но на этой странице `.is-active` пину никто не вешает |

### 2.3. Почему при переходе из вкладки пин не подсвечивается

Ранжированные причины (все проверены на HEAD 77fe00b):

1. **Главная: ссылки вкладки не содержат якоря.** `_issues_panel.html:113` («Требуют внимания») и `:229` (основная таблица): `href="...document_detail...?page={{ issue.page_number }}"` — без `#issue-...`. Grep по всем шаблонам: генераторов `#issue-` нет; якорь есть только на стороне потребителя (`document_detail.html:813`). Следствие: `_goToPageThenHighlight` парсит `issueId = null` (`:812-815`) → после `goToPage` ветка подсветки пропускается (`:842` `if (issueId && ...)`) → работает только переход на страницу. Именно это и наблюдается («page=N работает, пин нет»).
   *Примечание для Координатора: в постановке задачи сказано, что якорь передаётся и карточка открывается. На HEAD это не так — вероятно, в локальной сборке якорь добавлен руками и не закоммичен. План ниже закрывает оба сценария (с якорем и без).*
2. **Даже с якорем подсветка — разовый класс, а не состояние.** Цепочка `_goToPageThenHighlight` → 500 мс → `openIssueCard` → `window.selectIssue` → `centerMarkup` вешает `.border-danger` на конкретный DOM-узел. Любая перерисовка листа — `setZoom`/`rotatePage` (`renderPages`), смена статуса (`issue:status-changed` → `drawMarks` `pdf_viewer.js:2837-2852`), гонка двух начальных рендеров — **сносит узлы** (`drawMarksInto:305-308`) и рисует новые без `border-danger`. Восстановить его некому: `selectedIssueId` равен null (см. причину 3).
3. **Правильный механизм персистентности не подключён.** `drawMarksInto:374-381` умеет восстанавливать `.is-active` после каждой перерисовки — но только для `selectedIssueId`, который на странице `document_detail` никто не выставляет (внутренняя `selectIssue` мертва, `pdf_viewer.js:2863-2865` init-вызов тоже уходит в early-return).
4. **Кластеры — тихий пропуск.** Если пин попал в кластер (`CLUSTER_THRESHOLD_PX = 28`), DOM-узла `.pdf-markup[data-issue-id]` не существует — `centerMarkup` молча возвращается (`sync.js:7-9`), кластерный бейдж не имеет `data-issue-id` (`pdf_viewer.js:491-530`). Подсветки нет и при рабочей цепочке.
5. **Хрупкость `_goToPageThenHighlight`.** (а) `afterPage = goToPage(pageNum)` без обёртки `Promise.resolve` — если `goToPage` вернул `undefined` (pdfDocument ещё не готов), `afterPage.then` бросает TypeError и подсветка не наступает никогда; (б) магическая задержка 500 мс вместо ожидания факта отрисовки; (в) poll считает PDF готовым по появлению `.pdf-page`/изменению `#totalPages` (`:819-834`) — это происходит **до** завершения canvas-рендера; (г) дублирующий `card.scrollIntoView` + `card.classList.add('is-active')` (`:846-850`) дублируют то, что уже делает `openIssueCard`.

### 2.4. Хронология

Коммит **b6f361a** «fix(issues): navigate to pdf page from issue tab (**pin highlight to backlog**)» — добавил `?page=` в оба href и `_goToPageThenHighlight`; подсветка пина была явно отложена в backlog. Настоящая задача — закрыть этот backlog.

---

## 3. Решение

### 3.1. Принцип

1. **Единая точка подсветки** — новая функция `focusIssue(issueId, options)` внутри IIFE `pdf_viewer.js`: она одна выставляет `selectedIssueId`, перерисовывает пины и вешает классы. Всё остальное (sync.js, inline-скрипт шаблона) делегирует ей.
2. **Персистентность через существующий механизм**: `selectedIssueId` + ре-apply в `drawMarksInto:374-381` — подсветка автоматически выживает zoom/поворот/перерисовку статуса. Никаких «вторичных» разовых классов в сторонних файлах.
3. **Два визуальных уровня**: персистентная красная рамка `.is-active` (выбранный пин, переживает перерисовки) + транзиентный пульс `.is-flash` (1.2 с × 3, снимается по `animationend` — «где смотреть»).
4. **Кластеры — полноправные цели**: бейдж кластера получает `data-issue-ids`, подсвечивается вместе с пинами.
5. Обратная совместимость: `window.centerMarkup` сохраняется как тонкая обёртка (кто-то мог вызвать из консоли/расширений), `window.selectIssue` сохраняет сигнатуру `(event, issueId)`.

### 3.2. Карта изменений по файлам

| # | Файл | Тип | Что меняется |
|---|---|---|---|
| 1 | `static/js/pdf_viewer.js` | JS | + `clearPinHighlight()`, + `focusIssue(issueId, options)` (+ экспорт `window.focusIssue`); рефакторинг внутренней `selectIssue`: блок панели деталей — под guard `if (details)`, вызов `focusIssue` — всегда; + `badge.dataset.issueIds` в `drawClusterBadge`; guard `comparison.active` в `focusIssue`. Bump `?v=` в шаблоне |
| 2 | `static/js/sync.js` | JS | `selectIssue`: после `goToPage` вызывать `window.focusIssue(issueId, {navigate: false})` вместо `centerMarkup`; `selectIssueFromPdf`: делегировать в `focusIssue(issueId, {navigate: true})` (сохраняя `showIssueList` и подсветку строки). `centerMarkup` оставить как fallback/обёртку. Bump `?v=` |
| 3 | `storage/adapters/templates/storage/document_detail.html` | шаблон | `_goToPageThenHighlight` v2: guard `Promise.resolve`, `pageNum` из `data-page` карточки при отсутствии `?page`, вызов `openIssueCard(issueId)` сразу после рендера (подсветка приедет через цепочку `openIssueCard → selectIssue → focusIssue`), убрать дубль `scrollIntoView/is-active`, убрать слепые 500 мс (оставить короткий wait-страховку). Bump `?v=` у sync.js/pdf_viewer.js |
| 4 | `issues/templates/issues/_issues_panel.html` | шаблон | Добавить якорь в оба href: `?page={{ ... }}#issue-{{ issue.pk }}`; обернуть `?page=`/якорь в `{% if %}` для замечаний без пина; (опционально) ссылка «Документ» `:118` — тоже с якорем |
| 5 | `static/css/app.css` | CSS | Усилить `.pdf-markup.is-active` (красная рамка `!important` поверх inline border-color, свечение, `z-index` выше кластера); + `.pdf-markup.is-flash` и `.pdf-pin-cluster.is-flash` с `@keyframes issue-pin-pulse`; + `.pdf-pin-cluster.is-active`; + `@media (prefers-reduced-motion: reduce)`. Bump `?v=` |

Модель, вьюхи, сервисы, миграции — **не трогаются**.

### 3.3. `focusIssue` — псевдокод (файл `pdf_viewer.js`, вставка рядом с `selectIssue`/`scrollToMark`)

```js
function clearPinHighlight() {
    // Снять все визуальные состояния подсветки со всех пинов и кластеров.
    document.querySelectorAll(
        '.pdf-markup.is-active, .pdf-markup.is-flash, .pdf-markup.border-danger'
    ).forEach(function (m) {
        m.classList.remove('is-active', 'is-flash', 'border-danger');
    });
    document.querySelectorAll('.pdf-pin-cluster.is-active, .pdf-pin-cluster.is-flash')
        .forEach(function (b) { b.classList.remove('is-active', 'is-flash'); });
}

function focusIssue(issueId, options) {
    const issue = findIssue(issueId);
    if (!issue || !issue.page_number) return Promise.resolve(false); // без пина подсвечивать нечего
    if (comparison.active) return Promise.resolve(false);            // режим сравнения: пинов нет

    const navigate = !(options && options.navigate === false);

    const doFocus = function () {
        selectedIssueId = Number(issue.id);      // персистентность: drawMarksInto:374-381 восстановит is-active при каждой перерисовке
        clearPinHighlight();
        drawMarks(Number(issue.page_number));    // перерисовать текущий лист (drawMarks сам гвардит isCurrentView)

        const page = pages.get(Number(issue.page_number));
        if (!page) return false;
        const mark = page.layer.querySelector(
            '.pdf-markup[data-issue-id="' + issue.id + '"]'
        );
        if (mark) {
            mark.scrollIntoView({ behavior: 'smooth', block: 'center' });
            mark.classList.add('is-flash');      // транзиентный пульс
            mark.addEventListener('animationend', function () {
                mark.classList.remove('is-flash');
            }, { once: true });
        } else {
            // Пин схлопнут в кластер — подсвечиваем бейдж кластера (§3.6).
            const cluster = page.layer.querySelector(
                '.pdf-pin-cluster[data-issue-ids~="' + issue.id + '"]'
            );
            if (cluster) {
                cluster.classList.add('is-active', 'is-flash');
                cluster.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }
        return true;
    };

    if (navigate && Number(window.getCurrentPage()) !== Number(issue.page_number)) {
        const result = window.goToPage(Number(issue.page_number));
        if (result && typeof result.then === 'function') {
            return result.then(doFocus);
        }
    }
    return Promise.resolve(doFocus());
}
window.focusIssue = focusIssue;
```

Рефакторинг внутренней `selectIssue` (`pdf_viewer.js:145-238`) — минимальный:

```js
function selectIssue(issueId, options) {
    const issue = findIssue(issueId);
    if (!issue) return;
    focusIssue(issue.id, { navigate: options ? options.scrollMark !== false : true }); // пин: всегда
    if (!details) return;                    // ← ранний выход ТОЛЬКО для панели деталей (сейчас он убивает и пин)
    /* …существующий код заполнения issueElement/detailId/… без изменений… */
}
```

Инициализация `selectIssue(issues[0].id)` (`:2863-2865`) продолжит работать на обеих раскладках (на `document_detail` — только пин, панель не трогается).

### 3.4. `sync.js`

```js
window.selectIssue = function (event, issueId) {
    if (event && event.target.closest('button')) return;
    /* …существующие снятия table-active/border-danger и подсветка строки… */
    const finish = function () {
        if (typeof window.focusIssue === 'function') {
            window.focusIssue(issueId, { navigate: false }); // страницу уже перешли выше
        } else {
            window.centerMarkup(issueId);                    // fallback
        }
    };
    /* …существующий блок goToPage + finish… */
};

window.selectIssueFromPdf = function (issueId) {
    /* …showIssueList + подсветка строки… */
    if (typeof window.focusIssue === 'function') {
        window.focusIssue(issueId, { navigate: true });
    } else {
        window.centerMarkup(issueId);
    }
};
// window.centerMarkup НЕ удалять — оставить как обёртку (совместимость).
```

### 3.5. `document_detail.html` — `_goToPageThenHighlight` v2

```js
function _goToPageThenHighlight() {
    const hash = window.location.hash;
    const issueId = hash && hash.startsWith('#issue-') ? hash.replace('#issue-', '') : null;
    const urlPage = Number(new URLSearchParams(window.location.search).get('page')) || 0;
    if (!urlPage && !issueId) return;

    // Якорь есть, ?page= нет — берём страницу из data-page карточки.
    let pageNum = urlPage;
    if (!pageNum && issueId) {
        const card = document.querySelector('#list .card[data-id="' + issueId + '"]');
        pageNum = card && card.dataset.page ? Number(card.dataset.page) : 0;
    }

    const initialTotal = (document.getElementById('totalPages') || {}).textContent || '1';
    function poll() {                              /* …существующий poll до .pdf-page/totalPages… */
        /* … */
        const afterPage = Promise.resolve(         // ← защита от undefined-возврата goToPage
            (pageNum > 0 && typeof window.goToPage === 'function')
                ? window.goToPage(pageNum) : null
        );
        afterPage.then(function () {
            if (!issueId) return;
            // drawMarks уже выполнен в хвосте promise рендера (pdf_viewer.js:951-957),
            // поэтому магические 500 мс больше не нужны. Подсветка пина приедет по цепочке
            // openIssueCard → window.selectIssue → focusIssue.
            if (typeof openIssueCard === 'function') {
                openIssueCard(issueId);
            }
        });
    }
    setTimeout(poll, 200);
}
```

Дубли из текущей версии (`card.scrollIntoView`, `card.classList.add('is-active')`) удалить — `openIssueCard` уже делает и то и другое (`document_detail.html:744-751`).

### 3.6. Кластеры

В `drawClusterBadge` (`pdf_viewer.js:491-530`) добавить одну строку рядом с `badge.textContent`:

```js
badge.dataset.issueIds = cluster.items.map(function (i) { return String(i.issue.id); }).join(' ');
```

Селектор `[data-issue-ids~="7"]` (§3.3) находит кластер по id замечания. Опция на будущее (вне MVP): при подсветке кластера автоматически вызывать `cluster.click()` — зум к группе/раскрытие попапа.

### 3.7. CSS (`app.css`, блок рядом с `:2475`)

```css
/* Активный (выбранный) пин — персистентная красная рамка поверх inline border-color */
.pdf-markup.is-active {
    border-color: var(--s-attention-solid, #B44E48) !important;
    box-shadow: 0 0 0 3px rgba(180, 78, 72, 0.45);
    z-index: 7;                                   /* выше кластерного бейджа (z-index: 6) */
}

/* Транзиентный пульс — «где смотреть», снимается по animationend */
@keyframes issue-pin-pulse {
    0%   { box-shadow: 0 0 0 0 rgba(180, 78, 72, 0.55); }
    70%  { box-shadow: 0 0 0 12px rgba(180, 78, 72, 0); }
    100% { box-shadow: 0 0 0 0 rgba(180, 78, 72, 0); }
}
.pdf-markup.is-flash,
.pdf-pin-cluster.is-flash {
    animation: issue-pin-pulse 1.2s ease-out 3;
}
.pdf-pin-cluster.is-active {
    outline: 3px solid rgba(180, 78, 72, 0.6);
    outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
    .pdf-markup.is-flash,
    .pdf-pin-cluster.is-flash { animation: none; }
}
```

Примечание: `!important` легитимно перекрывает inline `border-color` (js `:441`), потому что inline-стили без `!important` проигрывают CSS-правилам с `!important`. Старое правило `.pdf-markup.is-active` (`app.css:2364-2366`) заменяется этим (голубое свечение акцента на пинах больше не используется).

---

## 4. Пошаговый план для Кодера

| Шаг | Файл | Действие | Проверка |
|---|---|---|---|
| 1 | `pdf_viewer.js` | Ввести `clearPinHighlight` + `focusIssue` + `window.focusIssue`; рефакторинг `selectIssue` (guard `if (details)` только вокруг панели; вызов `focusIssue` до раннего выхода) | В консоли на странице документа: `focusIssue(<id>)` — пин краснеет, пульсирует, лист центрируется |
| 2 | `pdf_viewer.js` | `drawClusterBadge`: `badge.dataset.issueIds = ...` | Соседние пины (<28 px) схлопываются в кластер с `data-issue-ids` в DOM |
| 3 | `pdf_viewer.js` | Guard `comparison.active` в `focusIssue` (см. §3.3) | В режиме «Сравнить» вызов не падает и ничего не рисует |
| 4 | `sync.js` | `selectIssue`/`selectIssueFromPdf` → `focusIssue` (fallback `centerMarkup`); `centerMarkup` сохранить | Клик по карточке и по пину: подсветка появляется и **сохраняется после зума/поворота** |
| 5 | `document_detail.html` | `_goToPageThenHighlight` v2 (§3.5): `Promise.resolve`-guard, `pageNum` из карточки, убрать 500 мс и дубли; bump `?v=` у sync.js и pdf_viewer.js | Прямая ссылка `/documents/<id>/?page=N#issue-K` открывает страницу N, пин подсвечен, карточка открыта |
| 6 | `_issues_panel.html` | Якоря в href `:113` и `:229` + `{% if %}`-обёртка для замечаний без `page_number` | Клик из вкладки «Замечания» проекта → документ на нужной странице с подсвеченным пином |
| 7 | `app.css` | CSS из §3.7; bump `?v=` у app.css в подключении | Пульс виден ~3.6 с и гаснет; рамка остаётся; при `prefers-reduced-motion` анимации нет |

После шагов: `python manage.py test issues storage --noinput`, `grep -rn "RuntimeWarning" лог тестов`, ручная проверка по чек-листу §7. Бэкенд-тесты не должны измениться вообще (правки только фронт).

---

## 5. Граничные случаи

| Случай | Поведение по плану |
|---|---|
| Замечание без пина (`page_number = NULL`) | Ссылка из вкладки — без `?page`/якоря (`{% if %}` в шаблоне); `focusIssue` возвращает `false`; документ открывается без подсветки. CheckConstraint гарантирует: `page_number` задан ⟹ координаты заданы |
| Просмотр неактуальной версии (`?version=…`, `isCurrentView = false`) | `drawMarks` no-op (`pdf_viewer.js:251-253`) — пинов нет; `focusIssue` вернёт `false`. Опционально: тост «Пины доступны только в актуальной версии» (механика `viewerNotify` `:14-34` уже есть) |
| Пин в кластере (2+ пина ближе 28 px) | Подсвечивается бейдж кластера (`.is-active` + `.is-flash`); разворачивание попапа — вне MVP |
| Режим «Сравнить» | `focusIssue` no-op при `comparison.active` |
| Ссылка без якоря (`?page=N`) | Только переход на страницу — поведение не меняется (регрессии нет) |
| Якорь без `?page=` (`/documents/N/#issue-K`) | `_goToPageThenHighlight` берёт страницу из `data-page` карточки и делает `goToPage` сама |
| Публичная share-ссылка (аноним) | Панель замечаний не рендерится; `openIssueCard` вернёт early-return (`#list` отсутствует); `focusIssue` подсветит пин, если вьювер отрендерен. Падений нет |
| Обновление страницы (F5) | URL сохраняется → подсветка повторяется — осознанное поведение |
| Смена статуса замечания из карточки | `issue:status-changed` → `drawMarks` перекрашивает пины; `.is-active` восстанавливается механизмом `selectedIssueId` — рамка не исчезает |

---

## 6. Риски и совместимость

| Риск | Митигирование |
|---|---|
| `!important` в `.is-active` перекроет inline-цвет рамки у всех подсвеченных пинов | Приемлемо: класс вешается только на одну цель; статусный цвет остаётся на бейдже. Проверить кластерные попапы визуально |
| Двойной скролл: `focusViewerPage` (после `goToPage`) + `scrollIntoView` пина | Оба к одной цели; пиновый скролл позже и приоритетнее. Если дрожание заметно — вызвать `focusIssue` до `goToPage().then(focusViewerPage)`-скролла нельзя; оставить как есть (визуально это «доводка» к пину) |
| `selectedIssueId` управляется извне | Сигнатура внутренней `selectIssue` сохранена; init-вызов `:2863-2865` не ломается; на `my_issues`/реестре раскладка не меняется |
| Кэш статики | Обязательный bump `?v=` у трёх файлов (`pdf_viewer.js`, `sync.js`, `app.css`) — по образцу существующих версий (`?v=20261112-t8`) |
| HTMX-переходы вкладки без перезагрузки | Сейчас вкладка меняется полной навигацией — ок. Если когда-нибудь панели станут HTMX-целями, добавить слушатель `hashchange` (follow-up, вне MVP) |
| Ошибки в консоли при отсутствии `#list`/`issues-data` | Все новые функции гвардят отсутствие DOM-узлов; `focusIssue` возвращает promise<boolean> |

---

## 7. Чек-лист приёмки

- [ ] Клик из «Требуют внимания» (вкладка «Замечания» проекта) → документ на странице N, пин целевого замечания: красная рамка + пульс ~3.6 с, рамка остаётся; карточка справа открыта.
- [ ] Клик из основной таблицы вкладки → то же самое.
- [ ] Ручная ссылка `…/documents/<id>/?page=M#issue-K` → то же самое.
- [ ] Ссылка `…/documents/<id>/?page=M` (без якоря) → страница открывается, подсветки нет, ошибок нет.
- [ ] Якорь без `?page=` → переход на страницу из `data-page` + подсветка.
- [ ] После зума (±) и поворота рамка сохраняется; пульс погас.
- [ ] Смена статуса замечания в карточке → цвет пина обновился, рамка выбранного не пропала.
- [ ] Целевой пин рядом с другими (кластер) → подсвечен бейдж кластера; при разводе зумом — подсвечивается сам пин.
- [ ] Замечание без пина → документ открывается, JS без ошибок.
- [ ] `?version=` на старую версию → страница открывается, пинов нет, JS без ошибок.
- [ ] Публичная share-ссылка → без ошибок в консоли.
- [ ] `python manage.py test issues storage --noinput` — зелёный; `RuntimeWarning` не появился.
- [ ] Клик по пину и по карточке (старые сценарии) работают как раньше, но подсветка стала персистентной.

## 8. Что сознательно не делаем

- Не трогаем бэкенд: модели (`Issue`), вьюхи, сервисы, миграции, URL — правки только в 2 JS-файлах, 1 inline-скрипте шаблона, 1 panel-шаблоне и CSS.
- Не переходим на annotation layer / ref-слой PDF.js — текущая DOM-отрисовка пинов сохраняется (она уже решает повороты и кластеризацию).
- Не вводим новых зависимостей и не трогаем service worker (`sw.js`) — только bump версий `?v=`.
- Не делаем SPA/hashchange-роутинг и не переносим `_goToPageThenHighlight` в отдельный файл (остаётся inline, как сейчас).
- Не дублируем логику подсветки во втором месте: `centerMarkup` остаётся только как совместимая обёртка.
