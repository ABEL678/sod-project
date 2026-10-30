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

    const markup = document.querySelector(
        '.pdf-markup[data-issue-id="' + issueId + '"]'
    );
    if (markup) {
        markup.classList.add('border-danger');
        markup.scrollIntoView({behavior: 'smooth', block: 'nearest'});
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

    const markup = document.querySelector(
        '.pdf-markup[data-issue-id="' + issueId + '"]'
    );
    if (markup) {
        markup.classList.add('border-danger');
    }
};

window.changeStatus = function (issueId, newStatus) {
    const csrfInput = document.querySelector(
        'input[name="csrfmiddlewaretoken"]'
    );
    const token = csrfInput ? csrfInput.value : '';
    fetch('/issues/' + issueId + '/status/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': token,
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({status: newStatus}),
        credentials: 'same-origin',
    }).then(function (response) {
        if (response.ok) {
            window.location.reload();
            return;
        }
        return response.json().then(function (data) {
            throw new Error(data.error || 'Ошибка смены статуса');
        });
    }).catch(function (error) {
        window.alert(error.message);
    });
};

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
