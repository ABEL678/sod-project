(function () {
    'use strict';

    function csrfToken() {
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        if (match) {
            return decodeURIComponent(match[1]);
        }
        var input = document.querySelector('[name=csrfmiddlewaretoken]');
        return input ? input.value : '';
    }

    function updateBellCount(count) {
        var bell = document.querySelector('.notif-bell');
        var counter = bell ? bell.querySelector('.notif-count') : null;
        if (!counter) {
            return;
        }
        counter.textContent = count;
        counter.hidden = count === 0;
    }

    function isSameLocation(href) {
        try {
            var target = new URL(href, window.location.href);
            return target.pathname === window.location.pathname
                && target.search === window.location.search;
        } catch (error) {
            return false;
        }
    }

    document.addEventListener('click', function (event) {
        if (!event.target || !event.target.closest) {
            return;
        }
        var item = event.target.closest('.notif-item');
        if (!item) {
            return;
        }
        var href = item.getAttribute('href');
        var markUrl = item.dataset.markUrl;
        if (!markUrl) {
            return;
        }
        if (event.button !== 0 || event.ctrlKey || event.metaKey
                || event.shiftKey || event.altKey) {
            return;
        }
        event.preventDefault();

        // Оптимистичный UI до сети: снять подсветку и бейдж «Новое».
        item.classList.remove('is-unread');
        var badge = item.querySelector('[data-notif-new-badge]');
        if (badge) {
            badge.hidden = true;
        }

        var samePage = isSameLocation(href);
        if (!samePage) {
            // Страховка: переход по таймеру, если ответ завис.
            window.setTimeout(function () {
                window.location.assign(href);
            }, 400);
        }

        fetch(markUrl, {
            method: 'POST',
            keepalive: true,
            headers: {
                'X-CSRFToken': csrfToken(),
                'X-Requested-With': 'XMLHttpRequest'
            }
        }).then(function (response) {
            if (!response.ok) {
                console.warn('notifications: mark-read HTTP ' + response.status);
                return null;
            }
            return response.json();
        }).then(function (data) {
            if (data && typeof data.unread_count === 'number') {
                updateBellCount(data.unread_count);
            }
            if (!samePage && href) {
                window.location.assign(href);
            }
        }).catch(function (error) {
            console.warn('notifications: mark-read failed', error);
        });
    });
})();
