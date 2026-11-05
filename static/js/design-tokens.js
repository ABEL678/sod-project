/* АВТОГЕНЕРАТ из design-tokens/tokens.json — НЕ РЕДАКТИРОВАТЬ ВРУЧНУЮ.
   Правки вносить в tokens.json, затем: python scripts/build_design_tokens.py */
/* Подключать ДО pdf_viewer.js: цвета статусов замечаний берутся отсюда,
   чтобы бейдж в панели, маркер на листе и рамка в экспорте PDF совпадали. */
(function () {
    'use strict';

    window.CDE_TOKENS = {
        version: '20261109.2',
        /* HEX-цвет маркера по коду статуса (для canvas и рамок) */
        statusHex: {
            CANCELLED: '#3F3F3C',
            CLOSED: '#5D6770',
            DISCUSSION: '#9A650F',
            DONE: '#3F7D56',
            IN_PROGRESS: '#2E7D8C',
            NEW: '#4674A4',
            REJECTED: '#B44E48',
        },
        /* CSS-класс бейджа по коду статуса */
        statusClass: {
            CANCELLED: 'cde-badge cde-badge--canceled',
            CLOSED: 'cde-badge cde-badge--closed',
            DISCUSSION: 'cde-badge cde-badge--review',
            DONE: 'cde-badge cde-badge--done',
            IN_PROGRESS: 'cde-badge cde-badge--progress',
            NEW: 'cde-badge cde-badge--wait',
            REJECTED: 'cde-badge cde-badge--attention',
        },
        defaultStatus: 'NEW'
    };
})();
