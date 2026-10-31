/**
 * CDE «Среда общих данных» — конфигурация Tailwind
 * Источник истины: tokens.json · Версия 1.0 · 31.10.2026
 *
 * Tailwind v3:  module.exports = {...}  — подключается автоматически.
 * Tailwind v4:  подключите JS-конфиг через  @config "./tailwind.config.js";
 *               либо перенесите значения в CSS-блок @theme (см. сниппет внизу файла).
 */

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{html,ts,tsx,js,jsx,vue}'],
  theme: {
    extend: {
      colors: {
        /* Поверхности */
        app: 'var(--c-bg-app)',
        surface: {
          DEFAULT: 'var(--c-bg-surface)',
          hover: 'var(--c-bg-surface-hover)',
          sunken: 'var(--c-bg-surface-sunken)',
        },
        canvas: 'var(--c-bg-canvas)',

        /* Границы */
        hairline: 'var(--c-border-hairline)',
        line: 'var(--c-border-default)',

        /* Текст */
        ink: {
          DEFAULT: 'var(--c-text-primary)',
          secondary: 'var(--c-text-secondary)',
          muted: 'var(--c-text-muted)',
          'on-dark': 'var(--c-text-on-dark)',
        },

        /* Единственный акцент */
        accent: {
          DEFAULT: 'var(--c-accent)',
          hover: 'var(--c-accent-hover)',
          pressed: 'var(--c-accent-pressed)',
          subtle: 'var(--c-accent-subtle)',
          'on-subtle': 'var(--c-accent-on-subtle)',
        },

        /* Статусы: один смысл — один цвет (панель = бейдж = маркер на листе) */
        status: {
          attention: { DEFAULT: 'var(--s-attention-solid)', subtle: 'var(--s-attention-subtle)', on: 'var(--s-attention-on)' }, // Не принято (REJECTED)
          wait:      { DEFAULT: 'var(--s-wait-solid)',      subtle: 'var(--s-wait-subtle)',      on: 'var(--s-wait-on)' },      // Открыто (NEW)
          progress:  { DEFAULT: 'var(--s-progress-solid)',  subtle: 'var(--s-progress-subtle)',  on: 'var(--s-progress-on)' },  // В работе (IN_PROGRESS)
          review:    { DEFAULT: 'var(--s-review-solid)',    subtle: 'var(--s-review-subtle)',    on: 'var(--s-review-on)' },    // К обсуждению (DISCUSSION)
          done:      { DEFAULT: 'var(--s-done-solid)',      subtle: 'var(--s-done-subtle)',      on: 'var(--s-done-on)' },      // Выполнено (DONE)
          closed:    { DEFAULT: 'var(--s-closed-solid)',    subtle: 'var(--s-closed-subtle)',    on: 'var(--s-closed-on)' },    // Закрыто (CLOSED)
          canceled:  { DEFAULT: 'var(--s-canceled-solid)',  subtle: 'var(--s-canceled-subtle)',  on: 'var(--s-canceled-on)' },  // Аннулировано (CANCELLED)
        },
      },

      fontFamily: {
        sans: ['"Golos Text"', 'Inter', '-apple-system', '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"SF Mono"', 'Consolas', '"Liberation Mono"', 'monospace'],
      },

      /* Шкала: [размер, line-height, weight] */
      fontSize: {
        display:   ['22px', { lineHeight: '28px', fontWeight: '600' }], // заголовок экрана
        heading:   ['18px', { lineHeight: '24px', fontWeight: '600' }], // заголовок панели
        subhead:   ['16px', { lineHeight: '22px', fontWeight: '600' }],
        'body-lg': ['15px', { lineHeight: '22px' }],                     // тексты замечаний
        body:      ['14px', { lineHeight: '20px' }],
        second:    ['13px', { lineHeight: '18px' }],                     // лейблы фильтров, БЕЗ CAPS
        caption:   ['12px', { lineHeight: '16px', fontWeight: '500' }],  // метаданные, бейджи
      },

      /* 8pt-сетка */
      spacing: {
        'ctl-sm': '28px', 'ctl-md': '32px', 'ctl-lg': '40px',
        'row-comfort': '48px', 'row-compact': '36px',
        'panel-remarks': '380px',
      },

      borderRadius: { sm: '6px', DEFAULT: '8px', lg: '12px', pill: '999px' },

      boxShadow: {
        1: 'var(--shadow-1)',
        2: 'var(--shadow-2)',
        3: 'var(--shadow-3)',
        doc: 'var(--shadow-doc)',
        ring: 'var(--focus-ring)',
      },

      transitionDuration: { fast: '120ms', DEFAULT: '180ms', slow: '240ms' },
    },
  },
  plugins: [],
};

/* ============================================================================
 * Tailwind v4 — эквивалент через CSS @theme (положить в основной CSS-файл):
 *
 * @theme {
 *   --color-app: #F6F6F4;
 *   --color-surface: #FFFFFF;
 *   --color-canvas: #26272B;
 *   --color-ink: #191917;
 *   --color-ink-secondary: #6E6E68;
 *   --color-ink-muted: #9C9C94;
 *   --color-accent: #2F5BEA;
 *   --color-accent-subtle: #ECF0FE;
 *   --color-status-attention: #dc3545;  --color-status-attention-subtle: #FDEBED;
 *   --color-status-wait: #0d6efd;       --color-status-wait-subtle: #E7F0FF;
 *   --color-status-progress: #0dcaf0;   --color-status-progress-subtle: #E0F8FC;
 *   --color-status-review: #ffc107;     --color-status-review-subtle: #FFF8E1;
 *   --color-status-done: #198754;       --color-status-done-subtle: #E6F4EE;
 *   --color-status-closed: #6c757d;     --color-status-closed-subtle: #F0F1F2;
 *   --color-status-canceled: #212529;   --color-status-canceled-subtle: #E9EAEA;
 *   --font-sans: "Golos Text", "Inter", -apple-system, sans-serif;
 *   --font-mono: "JetBrains Mono", Consolas, monospace;
 * }
 * =========================================================================== */
