/* АВТОГЕНЕРАТ из design-tokens/tokens.json — НЕ РЕДАКТИРОВАТЬ ВРУЧНУЮ.
   Правки вносить в tokens.json, затем: python scripts/build_design_tokens.py */
/* Подключать ДО pdf_viewer.js: цвета статусов замечаний берутся отсюда,
   чтобы бейдж в панели, маркер на листе и рамка в экспорте PDF совпадали. */
(function () {
    'use strict';

    window.CDE_TOKENS = {
        version: '20261102',
        /* HEX-цвет маркера по коду статуса (для canvas и рамок) */
        statusHex: {
            CANCELLED: '#212529',
            CLOSED: '#6c757d',
            DISCUSSION: '#ffc107',
            DONE: '#198754',
            IN_PROGRESS: '#0dcaf0',
            NEW: '#0d6efd',
            REJECTED: '#dc3545',
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
