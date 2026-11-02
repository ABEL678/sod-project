window.centerMarkup = function (issueId) {
    const container = document.querySelector('.stage')
        || document.getElementById('pdfViewer');
    const markup = document.querySelector(
        '.pdf-markup[data-issue-id="' + issueId + '"]'
    );
    if (!container || !markup) {
        return;
    }

    markup.classList.add('border-danger');
    const rect = markup.getBoundingClientRect();
    const contRect = container.getBoundingClientRect();
    container.scrollTo({
        left: container.scrollLeft
            + (rect.left - contRect.left)
            - (contRect.width / 2)
            + (rect.width / 2),
        top: container.scrollTop
            + (rect.top - contRect.top)
            - (contRect.height / 2)
            + (rect.height / 2),
        behavior: 'smooth',
    });
};

window.selectIssue = function (event, issueId) {
    if (event && event.target.closest('button')) {
        return;
    }

    document.querySelectorAll('.issue-row').forEach(function (row) {
        row.classList.remove('table-active');
    });
    document.querySelectorAll('.pdf-markup').forEach(function (markup) {
        markup.classList.remove('border-danger');
    });

    const row = document.getElementById('issue-row-' + issueId);
    if (row) {
        row.classList.add('table-active');
    }

    const card = document.querySelector(
        '.issue-row[data-issue-id="' + issueId + '"]'
    );
    const pageNumber = card ? Number(card.dataset.page) : 0;
    const finish = function () {
        window.centerMarkup(issueId);
    };

    if (
        pageNumber
        && typeof window.goToPage === 'function'
        && (
            typeof window.getCurrentPage !== 'function'
            || Number(window.getCurrentPage()) !== pageNumber
        )
    ) {
        const result = window.goToPage(pageNumber);
        if (result && typeof result.then === 'function') {
            result.then(finish);
        } else {
            finish();
        }
    } else {
        finish();
    }
};

window.selectIssueFromPdf = function (issueId) {
    document.querySelectorAll('.issue-row').forEach(function (row) {
        row.classList.remove('table-active');
    });
    document.querySelectorAll('.pdf-markup').forEach(function (markup) {
        markup.classList.remove('border-danger');
    });

    if (typeof window.showIssueList === 'function') {
        window.showIssueList();
    }

    const row = document.getElementById('issue-row-' + issueId);
    if (row) {
        row.classList.add('table-active');
        row.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }

    window.centerMarkup(issueId);
};

function csrfToken() {
    const csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return csrfInput ? csrfInput.value : '';
}

function setStatusVars(element, status) {
    const key = String(status).toLowerCase();
    element.style.setProperty('--sfg', 'var(--status-' + key + '-on)');
    element.style.setProperty('--sbg', 'var(--status-' + key + '-bg)');
}

function setMarkVars(element, status) {
    const key = String(status).toLowerCase();
    element.style.setProperty('--mk', 'var(--status-' + key + ')');
    element.style.setProperty('--mkb', 'var(--status-' + key + '-bg)');
    element.style.setProperty('--mk-text', 'var(--status-' + key + '-on)');
}

function refreshPublicationBanner() {
    const banner = document.getElementById('publicationBlockedBanner');
    if (!banner) {
        return;
    }
    const openStatuses = ['NEW', 'IN_PROGRESS', 'REJECTED'];
    let openCount = 0;
    document.querySelectorAll('.issue-row').forEach(function (row) {
        if (openStatuses.indexOf(row.dataset.status) !== -1) {
            openCount += 1;
        }
    });
    const counter = banner.querySelector('[data-open-issues-count]');
    if (counter) {
        counter.textContent = String(openCount);
    }
    banner.classList.toggle('d-none', openCount === 0);
}

function refreshIssueAfterStatusChange(issueId, newStatus, label) {
    // 1. Строка в списке: датасет, бейджи, маркер-шильдик.
    const row = document.getElementById('issue-row-' + issueId);
    if (row) {
        row.dataset.status = newStatus;
        if (label) {
            row.dataset.statusLabel = label;
        }
        row.querySelectorAll('.badge').forEach(function (badge) {
            if (label) {
                badge.textContent = label;
            }
            setStatusVars(badge, newStatus);
        });
        const markBadge = row.querySelector('.mark-badge');
        if (markBadge) {
            setMarkVars(markBadge, newStatus);
        }
    }

    // 2. Шапка карточки (если открыта).
    const headBadge = document.getElementById('dBadge');
    if (headBadge && document.getElementById('detail').style.display !== 'none') {
        if (label) {
            headBadge.textContent = label;
        }
        setStatusVars(headBadge, newStatus);
    }

    // 3. Счётчики табов.
    if (typeof window.updateIssueCounters === 'function') {
        window.updateIssueCounters();
    }

    // 4. Если активен фильтр статуса — скрыть/показать строку по фильтру.
    const activeTab = document.querySelector('#statusTabs .status-tab.active');
    if (
        row
        && activeTab
        && activeTab.dataset.filter !== 'all'
        && typeof window.statusGroup === 'function'
    ) {
        if (window.statusGroup(newStatus) !== activeTab.dataset.filter) {
            row.style.display = 'none';
        } else {
            row.style.display = '';
        }
    }

    // 5. Перерисовать маркеры на листе PDF (слушатель внутри pdf_viewer.js).
    document.dispatchEvent(new CustomEvent('issue:status-changed', {
        detail: {id: issueId, status: newStatus},
    }));

    // 6. Плашка блокировки публикации: пересчитать открытые замечания.
    refreshPublicationBanner();

    // 7. Обновить карточку: у новой статуса другая матрица допустимых переходов.
    const detail = document.getElementById('detail');
    if (
        detail
        && detail.style.display !== 'none'
        && typeof window.openIssueCard === 'function'
    ) {
        window.openIssueCard(issueId);
    }
}

window.changeStatus = function (issueId, newStatus, label) {
    fetch('/issues/' + issueId + '/status/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': csrfToken(),
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({status: newStatus}),
        credentials: 'same-origin',
    }).then(function (response) {
        if (response.ok) {
            refreshIssueAfterStatusChange(issueId, newStatus, label);
            return;
        }
        return response.json().then(function (data) {
            throw new Error(data.error || 'Ошибка смены статуса');
        });
    }).catch(function (error) {
        window.alert(error.message);
    });
};

// Комментарий без перезагрузки: перехватываем сабмит и обновляем карточку.
document.addEventListener('submit', function (event) {
    const form = event.target;
    if (!form.matches || !form.matches('form[action*="/comment/"]')) {
        return;
    }
    if (!document.getElementById('detail')) {
        return;
    }
    event.preventDefault();
    const match = form.action.match(/issues\/(\d+)\/comment\//);
    const issueId = match ? Number(match[1]) : null;
    const formData = new FormData(form);
    fetch(form.action, {
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
        headers: {
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': formData.get('csrfmiddlewaretoken') || csrfToken(),
        },
    }).then(function (response) {
        if (!response.ok) {
            throw new Error('Не удалось отправить комментарий');
        }
        if (issueId && typeof window.openIssueCard === 'function') {
            window.openIssueCard(issueId);
        }
    }).catch(function (error) {
        window.alert(error.message);
    });
});

function initTooltips(root) {
    if (!window.bootstrap || !window.bootstrap.Tooltip) {
        return;
    }
    (root || document).querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
        if (!window.bootstrap.Tooltip.getInstance(el)) {
            new window.bootstrap.Tooltip(el);
        }
    });
}

document.addEventListener('DOMContentLoaded', function () {
    initTooltips(document);
});

document.body.addEventListener('htmx:afterSwap', function (event) {
    initTooltips(event.target);
});
