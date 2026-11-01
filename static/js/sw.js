/* Stream-CAD service worker (Волна 3, PWA для площадки).
   Стратегии:
   - навигация (HTML): network-first, при офлайне — из кэша;
   - статика: stale-while-revalidate (мгновенно из кэша, тихо обновляем).
   Не кэшируем API/POST, палитру, уведомления и всё с query-параметрами. */
'use strict';

const CACHE_NAME = 'sod-shell-v4';

const PRECACHE_URLS = [
    '/static/css/bootstrap.min.css',
    '/static/design-tokens/tokens.css',
    '/static/css/redesign.css',
    '/static/css/app.css',
    '/static/js/vendor/bootstrap.bundle.min.js',
    '/static/js/design-tokens.js',
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
    /* Динамические данные всегда из сети (кэш — только фолбэк) */
    const dynamic = (
        url.pathname.startsWith('/palette/')
        || url.pathname.startsWith('/issues/')
        || url.pathname.startsWith('/projects/')
        || url.pathname.startsWith('/documents/')
        || url.pathname.startsWith('/download/')
        || url.pathname.startsWith('/media/')
        || url.pathname.startsWith('/folders/')
        || url.pathname.startsWith('/offline/')
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
