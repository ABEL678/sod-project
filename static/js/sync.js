window.selectIssue = function (event, issueId) {
    if (event && event.target.closest('button')) {
        return;
    }

    document.querySelectorAll('.table tr').forEach(function (row) {
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
        markup.scrollIntoView({behavior: 'smooth', block: 'center'});
    }
};

window.selectIssueFromPdf = function (issueId) {
    document.querySelectorAll('.table tr').forEach(function (row) {
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
        row.scrollIntoView({behavior: 'smooth', block: 'center'});
    }

    const markup = document.querySelector(
        '.pdf-markup[data-issue-id="' + issueId + '"]'
    );
    if (markup) {
        markup.classList.add('border-danger');
    }
};
