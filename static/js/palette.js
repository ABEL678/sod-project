/* Волна 3: командная палитра (Ctrl+K / Cmd+K).
   Поиск по проектам, документам и замечаниям, доступным пользователю:
   GET /palette/?q=... -> JSON. Навигация стрелками, Enter — переход. */
(function () {
    'use strict';

    var modal = null;
    var bootstrapModal = null;
    var input = null;
    var results = null;
    var activeIndex = -1;
    var flatItems = [];
    var searchTimer = null;
    var lastQuery = '';

    function init() {
        modal = document.getElementById('cmdkModal');
        if (!modal) {
            return;
        }
        input = document.getElementById('cmdkInput');
        results = document.getElementById('cmdkResults');
        if (window.bootstrap && window.bootstrap.Modal) {
            bootstrapModal = window.bootstrap.Modal.getOrCreateInstance(modal);
        }

        document.addEventListener('keydown', function (event) {
            if ((event.ctrlKey || event.metaKey) && (event.key === 'k' || event.key === 'K' || event.code === 'KeyK')) {
                event.preventDefault();
                open();
            } else if (event.key === 'Escape' && bootstrapModal) {
                /* bootstrap сам закрывает по Esc; только сбросим подсветку */
            }
        });

        if (input) {
            input.addEventListener('input', function () {
                clearTimeout(searchTimer);
                var query = input.value.trim();
                if (query.length < 2) {
                    render([]);
                    lastQuery = '';
                    return;
                }
                searchTimer = setTimeout(function () {
                    search(query);
                }, 180);
            });
            input.addEventListener('keydown', function (event) {
                if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    moveSelection(1);
                } else if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    moveSelection(-1);
                } else if (event.key === 'Enter') {
                    event.preventDefault();
                    var item = flatItems[activeIndex];
                    if (item) {
                        window.location.href = item.url;
                    }
                }
            });
        }

        if (results) {
            results.addEventListener('click', function (event) {
                var row = event.target.closest('[data-cmdk-url]');
                if (row) {
                    window.location.href = row.getAttribute('data-cmdk-url');
                }
            });
        }
    }

    function open() {
        if (bootstrapModal) {
            bootstrapModal.show();
        }
        if (input) {
            input.value = '';
            render([]);
            setTimeout(function () { input.focus(); }, 120);
        }
    }

    function search(query) {
        lastQuery = query;
        fetch('/palette/?q=' + encodeURIComponent(query), {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (input && input.value.trim() === lastQuery) {
                    render(normalize(data));
                }
            })
            .catch(function () { /* палитра не критична */ });
    }

    function normalize(data) {
        var groups = [];
        [['projects', 'Проекты'], ['documents', 'Документы'], ['issues', 'Замечания']]
            .forEach(function (pair) {
                var key = pair[0];
                var label = pair[1];
                var items = (data && data[key]) || [];
                if (items.length) {
                    groups.push({ label: label, items: items });
                }
            });
        return groups;
    }

    function render(groups) {
        flatItems = [];
        activeIndex = -1;
        if (!results) {
            return;
        }
        if (!groups.length) {
            results.innerHTML = '<div class="cmdk-empty">'
                + (input && input.value.trim().length >= 2
                    ? 'Ничего не найдено'
                    : 'Начните вводить шифр или название — минимум 2 символа')
                + '</div>';
            return;
        }
        var html = '';
        groups.forEach(function (group) {
            html += '<div class="cmdk-group-label">' + group.label + '</div>';
            group.items.forEach(function (item) {
                var index = flatItems.length;
                flatItems.push(item);
                html += '<button type="button" class="cmdk-item" role="option" '
                    + 'data-cmdk-url="' + encodeURI(item.url || '#') + '" data-index="' + index + '">'
                    + '<span class="cmdk-item-title"></span>'
                    + (item.meta ? '<span class="cmdk-item-meta"></span>' : '')
                    + '</button>';
            });
        });
        results.innerHTML = html;

        /* текст через textContent — защита от инъекций из названий */
        var buttons = results.querySelectorAll('.cmdk-item');
        var pointer = 0;
        groups.forEach(function (group) {
            group.items.forEach(function (item) {
                var button = buttons[pointer];
                pointer += 1;
                if (!button) {
                    return;
                }
                button.querySelector('.cmdk-item-title').textContent = item.title || '';
                var meta = button.querySelector('.cmdk-item-meta');
                if (meta) {
                    meta.textContent = item.meta || '';
                }
            });
        });
        moveSelection(0, true);
    }

    function moveSelection(delta, absoluteFirst) {
        if (!flatItems.length) {
            return;
        }
        if (absoluteFirst) {
            activeIndex = 0;
        } else {
            activeIndex = Math.min(flatItems.length - 1, Math.max(0, activeIndex + delta));
        }
        var buttons = results.querySelectorAll('.cmdk-item');
        buttons.forEach(function (button, index) {
            button.classList.toggle('is-active', index === activeIndex);
            if (index === activeIndex && button.scrollIntoView) {
                button.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
