## 2026-11-18 — Пин-подсветка замечаний: закрыто
План «persistent pin highlight» выполнен полностью (шаги 1-7).
- focusIssue: персистентная красная подсветка пина через selectedIssueId
  + ре-apply в drawMarksInto (выживает зум/поворот листа);
- фоллбек на кластерный бейдж через data-issue-ids (drawClusterBadge);
- scroll только внутри контейнера вьювера (scrollWithinViewport) —
  окно и шапка больше не скроллятся;
- sync.js: selectIssue/selectIssueFromPdf делегируют в focusIssue
  (fallback: centerMarkup);
- deep-link ?page=N#issue-K: нативный якорный прыжок отключён
  (якорь без id), history.scrollRestoration = 'manual', убран слепой
  setTimeout 500 мс и дубль подсветки в _goToPageThenHighlight;
- _issues_panel.html: ?page= и якорь только при issue.page_number
  (замечания без пина дают чистый URL);
- CSS: красный пульс (issue-pin-pulse), is-active согласован между
  app.css и viewer.css, prefers-reduced-motion уважается;
- bump'ы кэша: pdf_viewer p3, sync s2, viewer c1, app c2.
Коммит: ddd9711.
База: 77fe00b.
