/* Stream-CAD service worker (Волна 3, PWA для площадки; Спринт 3 — зачистка).
   Стратегии:
   - навигация (HTML): network-first, при офлайне — из кэша;
   - статика: stale-while-revalidate (мгновенно из кэша, тихо обновляем).
   Не кэшируем API/POST и всё с query-параметрами; префиксы динамических
   маршрутов приходят из сгенерированного sw-bypass-list.js (см. urls.py). */
'use strict';

/* Спринт 3 (P2): версия кэша собирается на сервере из хэша пре-кэшируемых
   файлов (storage.adapters.views.ServiceWorkerView). Ручной бамп v1→v4
   больше не нужен: поменяли статику — изменился sw.js — браузер сам
   переустановит воркер и соберёт свежий кэш. */
const CACHE_NAME = 'sod-shell-__CACHE_VERSION__';

/* Спринт 3 (P2): bypass-список генерируется из urls.py
   (scripts/build_design_tokens.py → sw-bypass-list.js). Прошитый ниже
   набор — только фолбэк на случай сбоя importScripts; в него включены
   префиксы, критичные для прав доступа. */
try {
    importScripts('/static/js/sw-bypass-list.js');
} catch (error) { /* используем фолбэк ниже */ }
const BYPASS_PREFIXES = self.SOD_SW_BYPASS || [
    /* Хотфикс: '/ai/' — HTMX-фрагменты ИИ-контура и превью находок;
       раньше они попадали в stale-while-revalidate, и панель/маркапы
       отдавались устаревшими из кэша. Фолбэк на случай сбоя
       importScripts должен совпадать с sw-bypass-list.js. */
    '/ai/',
    '/palette/',
    '/issues/',
    '/projects/',
    '/documents/',
    '/download/',
    '/media/',
    '/folders/',
    '/offline/',
    '/my-issues/',
    '/notifications/',
    '/admin/',
];

const PRECACHE_URLS = [
    '/static/css/bootstrap.min.css',
    '/static/design-tokens/tokens.css',
    '/static/css/redesign.css',
    '/static/css/app.css',
    '/static/fonts/fonts.css',
    '/static/fonts/files/golos-text-cyrillic.woff2',
    '/static/fonts/files/golos-text-cyrillic-ext.woff2',
    '/static/fonts/files/golos-text-latin.woff2',
    '/static/fonts/files/golos-text-latin-ext.woff2',
    '/static/fonts/files/jetbrains-mono-cyrillic.woff2',
    '/static/fonts/files/jetbrains-mono-cyrillic-ext.woff2',
    '/static/fonts/files/jetbrains-mono-latin.woff2',
    '/static/fonts/files/jetbrains-mono-latin-ext.woff2',
    '/static/fonts/bootstrap-icons/bootstrap-icons.min.css',
    '/static/fonts/bootstrap-icons/fonts/bootstrap-icons.woff2',
    '/static/js/vendor/bootstrap.bundle.min.js',
    '/static/js/design-tokens.js',
    '/static/js/sw-bypass-list.js',
    '/static/js/app.js',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/offline/',
];

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return Promise.allSettled(
                PRECACHE_URLS.map(function (url) {
                    return cache.add(url);
                })
            );
        }).then(function () {
            return self.skipWaiting();
        })
    );
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(
                keys
                    .filter(function (key) {
                        return key !== CACHE_NAME;
                    })
                    .map(function (key) {
                        return caches.delete(key);
                    })
            );
        }).then(function () {
            return self.clients.claim();
        })
    );
});

self.addEventListener('fetch', function (event) {
    const request = event.request;
    if (request.method !== 'GET') {
        return;
    }
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) {
        return;
    }
    /* Динамические данные всегда из сети (кэш — только фолбэк).
       Спринт 3: префиксы — из urls.py через sw-bypass-list.js, а не из
       рукописного списка, дрейфовавшего с маршрутами. */
    const dynamic = (
        BYPASS_PREFIXES.some(function (prefix) {
            return url.pathname.startsWith(prefix);
        })
        || url.search.length > 0
    );

    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(function () {
                return caches.match('/offline/').then(function (offline) {
                    return offline || caches.match(request) || Response.error();
                });
            })
        );
        return;
    }

    if (dynamic) {
        return;
    }

    /* Статика: stale-while-revalidate */
    event.respondWith(
        caches.open(CACHE_NAME).then(function (cache) {
            return cache.match(request).then(function (cached) {
                const network = fetch(request).then(function (response) {
                    if (response && response.status === 200) {
                        cache.put(request, response.clone());
                    }
                    return response;
                }).catch(function () {
                    return cached || Response.error();
                });
                return cached || network;
            });
        })
    );
});
