(function () {
    'use strict';

    window.zoomIn = window.zoomIn || function () {};
    window.zoomOut = window.zoomOut || function () {};
    window.rotatePage = window.rotatePage || function () {};
    window.togglePan = window.togglePan || function () {};
    window.toggleSelect = window.toggleSelect || function () {};
    window.copyText = window.copyText || function () {};

    const viewer = document.getElementById('pdfViewer');
    if (!viewer) {
        return;
    }

    const pdfUrl = viewer.dataset.pdfUrl;
    const createUrl = viewer.dataset.issueCreateUrl;
    const canCreate = viewer.dataset.canCreate === '1';
    const issuesData = document.getElementById('issues-data');
    const issues = issuesData ? JSON.parse(issuesData.textContent) : [];
    const issuesList = document.getElementById('issuesList');
    const issuesCount = document.getElementById('issuesCount');
    const modalElement = document.getElementById('issueModal');
    const form = document.getElementById('issueForm');
    const errorBox = document.getElementById('issueFormError');
    const coordinatesLabel = document.getElementById('issueCoordinates');
    const pageNumberInput = document.getElementById('issuePageNumber');
    const xRatioInput = document.getElementById('issueXRatio');
    const yRatioInput = document.getElementById('issueYRatio');
    const x1Input = document.getElementById('issueX1Ratio');
    const y1Input = document.getElementById('issueY1Ratio');
    const x2Input = document.getElementById('issueX2Ratio');
    const y2Input = document.getElementById('issueY2Ratio');
    const cropInput = document.getElementById('issueCropImage');
    const details = document.getElementById('issueDetails');
    const contextMenu = document.getElementById('issueContextMenu');
    const contextStatuses = document.getElementById('contextStatuses');
    const modal = modalElement && window.bootstrap
        ? window.bootstrap.Modal.getOrCreateInstance(modalElement)
        : null;

    const STATUS_COLORS = {
        NEW: '#0d6efd',
        IN_PROGRESS: '#0dcaf0',
        DISCUSSION: '#ffc107',
        REJECTED: '#dc3545',
        DONE: '#198754',
        CLOSED: '#6c757d',
        CANCELLED: '#212529',
    };

    const pages = new Map();
    let pdfDocument = null;
    let currentScale = 1.5;
    let currentRotation = 0;
    let panMode = false;
    let textMode = false;
    let selectedIssueId = null;
    let contextIssueId = null;
    let pendingCrop = '';

    function statusColor(status) {
        return STATUS_COLORS[status] || '#0d6efd';
    }

    function withAlpha(hex, alpha) {
        const value = hex.replace('#', '');
        const r = parseInt(value.slice(0, 2), 16);
        const g = parseInt(value.slice(2, 4), 16);
        const b = parseInt(value.slice(4, 6), 16);
        return 'rgba(' + r + ', ' + g + ', ' + b + ', ' + alpha + ')';
    }

    function escapeHtml(value) {
        const node = document.createElement('div');
        node.textContent = value == null ? '' : String(value);
        return node.innerHTML;
    }

    function urlFromTemplate(template, id) {
        return template.replace('/0/', '/' + id + '/');
    }

    function clearHighlight() {
        document.querySelectorAll('.issue-card.is-active').forEach(function (card) {
            card.classList.remove('is-active');
        });
        document.querySelectorAll('.pdf-markup.is-active, .pdf-pin.is-active').forEach(function (mark) {
            mark.classList.remove('is-active');
        });
    }

    function findIssue(issueId) {
        return issues.find(function (issue) {
            return Number(issue.id) === Number(issueId);
        });
    }

    function selectIssue(issueId, options) {
        const issue = findIssue(issueId);
        if (!issue || !details) {
            return;
        }
        selectedIssueId = issue.id;
        clearHighlight();
        const card = document.querySelector(
            '.issue-card[data-issue-id="' + issue.id + '"]'
        );
        if (card) {
            card.classList.add('is-active');
        }
        document.querySelectorAll(
            '[data-issue-id="' + issue.id + '"].pdf-markup, [data-issue-id="' + issue.id + '"].pdf-pin'
        ).forEach(function (mark) {
            mark.classList.add('is-active');
        });

        document.getElementById('issueElementEmpty').classList.add('d-none');
        document.getElementById('issueElement').classList.remove('d-none');
        document.getElementById('detailId').textContent = '#' + issue.id;
        const link = document.getElementById('detailElementLink');
        link.href = issue.document_url || window.location.pathname;
        link.textContent = 'Открыть PDF';
        document.getElementById('detailDescription').textContent = issue.description || '—';
        document.getElementById('detailDueDate').textContent = issue.due_date || '—';
        document.getElementById('detailAuthor').textContent = issue.author || '—';
        document.getElementById('detailDepartment').textContent = issue.department || '—';
        document.getElementById('detailAssignee').textContent = issue.assignee || '—';
        document.getElementById('detailOrganization').textContent = issue.project_organization || '—';
        document.getElementById('detailVersion').textContent = issue.version == null ? '—' : 'v' + issue.version;
        document.getElementById('detailFixDate').textContent = issue.actual_fix_date || '—';
        document.getElementById('detailRevision').textContent = issue.revision || '—';
        const status = document.getElementById('detailStatus');
        status.innerHTML = '<span class="badge" style="background:' + statusColor(issue.status) + '">'
            + escapeHtml(issue.status_label) + '</span>';
        document.getElementById('detailAnswer').value = issue.answer_text || '';

        const markup = document.getElementById('detailMarkup');
        if (issue.crop_image_url) {
            markup.innerHTML = '<a href="' + issue.crop_image_url + '" target="_blank">'
                + '<img src="' + issue.crop_image_url + '" class="issue-crop-thumb" alt="Маркап"></a>';
        } else if (issue.page_number) {
            markup.textContent = 'Страница ' + issue.page_number;
        } else {
            markup.textContent = '—';
        }

        const crop = document.getElementById('issueCrop');
        if (issue.crop_image_url) {
            crop.innerHTML = '<a href="' + issue.crop_image_url + '" target="_blank">'
                + '<img src="' + issue.crop_image_url + '" class="img-fluid border rounded" alt="Фрагмент"></a>';
        } else {
            crop.textContent = 'Вложение отсутствует.';
        }

        if (options && options.focusAnswer) {
            const tab = document.getElementById('elementTabBtn');
            if (tab && window.bootstrap) {
                window.bootstrap.Tab.getOrCreateInstance(tab).show();
            }
            document.getElementById('detailAnswer').focus();
        } else if (options && options.showFiles) {
            const tab = document.getElementById('filesTabBtn');
            if (tab && window.bootstrap) {
                window.bootstrap.Tab.getOrCreateInstance(tab).show();
            }
        }

        if (options && options.scrollMark) {
            scrollToMark(issue);
        }
    }

    function scrollToMark(issue) {
        const mark = document.querySelector(
            '.pdf-markup[data-issue-id="' + issue.id + '"], .pdf-pin[data-issue-id="' + issue.id + '"]'
        );
        if (mark) {
            mark.scrollIntoView({behavior: 'smooth', block: 'center'});
            mark.classList.add('is-active');
        }
    }

    function drawMarks(pageNumber) {
        const page = pages.get(Number(pageNumber));
        if (!page) {
            return;
        }
        page.layer.querySelectorAll('.pdf-markup, .pdf-pin').forEach(function (mark) {
            mark.remove();
        });
        issues
            .filter(function (issue) {
                return Number(issue.page_number) === Number(pageNumber);
            })
            .forEach(function (issue) {
                const color = statusColor(issue.status);
                if (
                    issue.x1_ratio != null && issue.y1_ratio != null
                    && issue.x2_ratio != null && issue.y2_ratio != null
                ) {
                    const mark = document.createElement('div');
                    mark.className = 'pdf-markup';
                    mark.dataset.issueId = issue.id;
                    mark.style.left = (issue.x1_ratio * 100) + '%';
                    mark.style.top = (issue.y1_ratio * 100) + '%';
                    mark.style.width = ((issue.x2_ratio - issue.x1_ratio) * 100) + '%';
                    mark.style.height = ((issue.y2_ratio - issue.y1_ratio) * 100) + '%';
                    mark.style.borderColor = color;
                    mark.style.backgroundColor = withAlpha(color, 0.16);
                    mark.title = issue.description;
                    mark.addEventListener('click', function (event) {
                        event.stopPropagation();
                        selectIssue(issue.id);
                    });
                    mark.addEventListener('contextmenu', function (event) {
                        event.preventDefault();
                        event.stopPropagation();
                        openContextMenu(event.clientX, event.clientY, issue.id);
                    });
                    page.layer.appendChild(mark);
                } else if (issue.x_ratio != null && issue.y_ratio != null) {
                    const pin = document.createElement('button');
                    pin.type = 'button';
                    pin.className = 'pdf-pin';
                    pin.dataset.issueId = issue.id;
                    pin.style.left = (issue.x_ratio * 100) + '%';
                    pin.style.top = (issue.y_ratio * 100) + '%';
                    pin.style.backgroundColor = color;
                    pin.title = issue.description;
                    pin.addEventListener('click', function (event) {
                        event.stopPropagation();
                        selectIssue(issue.id);
                    });
                    pin.addEventListener('contextmenu', function (event) {
                        event.preventDefault();
                        event.stopPropagation();
                        openContextMenu(event.clientX, event.clientY, issue.id);
                    });
                    page.layer.appendChild(pin);
                }
            });
        if (selectedIssueId != null) {
            document.querySelectorAll(
                '[data-issue-id="' + selectedIssueId + '"].pdf-markup, '
                + '[data-issue-id="' + selectedIssueId + '"].pdf-pin'
            ).forEach(function (mark) {
                mark.classList.add('is-active');
            });
        }
    }

    function appendIssueCard(issue) {
        if (!issuesList) {
            return;
        }
        const empty = issuesList.querySelector('.issues-empty');
        if (empty) {
            empty.remove();
        }
        const card = document.createElement('div');
        card.className = 'issue-card border rounded p-3 mb-2';
        card.id = 'issue-' + issue.id;
        card.dataset.issueId = issue.id;
        card.dataset.page = issue.page_number || '';
        card.dataset.statusOptions = JSON.stringify(issue.status_options || []);

        let statusButtons = '';
        (issue.status_options || []).forEach(function (option) {
            statusButtons += '<form method="post" action="'
                + urlFromTemplate(details.dataset.statusUrlTemplate, issue.id)
                + '">' + csrfInputHtml()
                + '<input type="hidden" name="status" value="' + escapeHtml(option.code) + '">'
                + '<button type="submit" class="btn btn-sm btn-outline-secondary">'
                + escapeHtml(option.label) + '</button></form>';
        });

        card.innerHTML =
            '<div class="d-flex justify-content-between align-items-start gap-2">'
            + '<div><div class="mb-1">'
            + '<span class="badge" style="background:' + statusColor(issue.status) + '">'
            + escapeHtml(issue.status_label) + '</span> '
            + '<span class="text-muted small">· ' + escapeHtml(issue.author) + '</span>'
            + (issue.assignee
                ? ' <span class="text-muted small">· адресат: ' + escapeHtml(issue.assignee) + '</span>'
                : '')
            + '</div><div>' + escapeHtml(issue.description) + '</div></div>'
            + '<div class="flex-shrink-0"><div class="d-flex flex-wrap gap-1 justify-content-end">'
            + statusButtons + '</div></div></div>';
        issuesList.prepend(card);
        if (issuesCount) {
            issuesCount.textContent = String(issues.length);
        }
    }

    function csrfInputHtml() {
        const csrfInput = form ? form.querySelector('input[name="csrfmiddlewaretoken"]') : null;
        return csrfInput
            ? '<input type="hidden" name="csrfmiddlewaretoken" value="'
                + escapeHtml(csrfInput.value) + '">'
            : '';
    }

    function openContextMenu(x, y, issueId) {
        if (!contextMenu || !details) {
            return;
        }
        contextIssueId = issueId;
        const issue = findIssue(issueId);
        if (!issue) {
            return;
        }
        contextStatuses.innerHTML = '';
        (issue.status_options || []).forEach(function (option) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'context-item context-status';
            button.textContent = option.label;
            button.dataset.status = option.code;
            contextStatuses.appendChild(button);
        });
        if (!(issue.status_options || []).length) {
            const disabled = document.createElement('div');
            disabled.className = 'context-label';
            disabled.textContent = 'Нет доступных переходов';
            contextStatuses.appendChild(disabled);
        }
        contextMenu.classList.remove('d-none');
        const rect = contextMenu.getBoundingClientRect();
        contextMenu.style.left = Math.min(x, window.innerWidth - rect.width - 8) + 'px';
        contextMenu.style.top = Math.min(y, window.innerHeight - rect.height - 8) + 'px';
    }

    function closeContextMenu() {
        if (contextMenu) {
            contextMenu.classList.add('d-none');
        }
        contextIssueId = null;
    }

    function openModal(pageNumber, rectangle, point, cropDataUrl) {
        if (!modal || !canCreate) {
            return;
        }
        pageNumberInput.value = pageNumber == null ? '' : pageNumber;
        xRatioInput.value = point ? point.x : '';
        yRatioInput.value = point ? point.y : '';
        x1Input.value = rectangle ? rectangle.x1 : '';
        y1Input.value = rectangle ? rectangle.y1 : '';
        x2Input.value = rectangle ? rectangle.x2 : '';
        y2Input.value = rectangle ? rectangle.y2 : '';
        cropInput.value = cropDataUrl || '';
        pendingCrop = cropDataUrl || '';
        if (coordinatesLabel) {
            if (rectangle) {
                coordinatesLabel.textContent = 'Маркап: страница ' + pageNumber
                    + ', область ' + Number(rectangle.x1).toFixed(3) + '…'
                    + Number(rectangle.x2).toFixed(3) + ' × '
                    + Number(rectangle.y1).toFixed(3) + '…'
                    + Number(rectangle.y2).toFixed(3);
            } else if (point) {
                coordinatesLabel.textContent = 'Страница ' + pageNumber
                    + ', x=' + Number(point.x).toFixed(3)
                    + ', y=' + Number(point.y).toFixed(3);
            } else {
                coordinatesLabel.textContent = 'Замечание без привязки к странице';
            }
        }
        if (errorBox) {
            errorBox.classList.add('d-none');
            errorBox.textContent = '';
        }
        modal.show();
    }

    function cropCanvas(canvas, rectangle) {
        const sx = Math.max(0, rectangle.x1 * canvas.width);
        const sy = Math.max(0, rectangle.y1 * canvas.height);
        const sw = Math.max(1, (rectangle.x2 - rectangle.x1) * canvas.width);
        const sh = Math.max(1, (rectangle.y2 - rectangle.y1) * canvas.height);
        const output = document.createElement('canvas');
        output.width = Math.round(sw);
        output.height = Math.round(sh);
        output.getContext('2d').drawImage(
            canvas, sx, sy, sw, sh, 0, 0, output.width, output.height
        );
        return output.toDataURL('image/jpeg', 0.85);
    }

    function bindPageInteraction(wrapper, canvas) {
        let dragStart = null;
        let preview = null;

        wrapper.addEventListener('mousedown', function (event) {
            if (event.button !== 0 || !canCreate || panMode || textMode) {
                return;
            }
            if (event.target.closest('.pdf-markup, .pdf-pin')) {
                return;
            }
            const rect = canvas.getBoundingClientRect();
            if (!rect.width || !rect.height) {
                return;
            }
            dragStart = {
                x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
                y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
                rect: rect,
                page: Number(wrapper.dataset.page),
            };
            preview = document.createElement('div');
            preview.className = 'pdf-markup pdf-markup-preview';
            preview.style.borderColor = STATUS_COLORS.NEW;
            preview.style.backgroundColor = withAlpha(STATUS_COLORS.NEW, 0.1);
            wrapper.querySelector('.pdf-pin-layer').appendChild(preview);
            event.preventDefault();
        });

        window.addEventListener('mousemove', function (event) {
            if (!dragStart || !preview) {
                return;
            }
            const rect = dragStart.rect;
            const x = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
            const y = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
            preview.style.left = (Math.min(dragStart.x, x) * 100) + '%';
            preview.style.top = (Math.min(dragStart.y, y) * 100) + '%';
            preview.style.width = (Math.abs(x - dragStart.x) * 100) + '%';
            preview.style.height = (Math.abs(y - dragStart.y) * 100) + '%';
        });

        window.addEventListener('mouseup', function (event) {
            if (!dragStart || !preview) {
                return;
            }
            const rect = dragStart.rect;
            const x = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
            const y = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
            const rectangle = {
                x1: Math.min(dragStart.x, x),
                y1: Math.min(dragStart.y, y),
                x2: Math.max(dragStart.x, x),
                y2: Math.max(dragStart.y, y),
            };
            const start = dragStart;
            preview.remove();
            preview = null;
            dragStart = null;

            if (rectangle.x2 - rectangle.x1 < 0.005 || rectangle.y2 - rectangle.y1 < 0.005) {
                openModal(start.page, null, {x: x, y: y}, '');
                return;
            }
            const crop = cropCanvas(canvas, rectangle);
            openModal(
                start.page,
                rectangle,
                {x: (rectangle.x1 + rectangle.x2) / 2, y: (rectangle.y1 + rectangle.y2) / 2},
                crop
            );
        });
    }

    function buildTextLayer(page, viewport, wrapper) {
        if (!window.pdfjsLib.renderTextLayer) {
            return;
        }
        const layer = document.createElement('div');
        layer.className = 'textLayer';
        layer.style.setProperty('--scale-factor', viewport.scale);
        const pinLayer = wrapper.querySelector('.pdf-pin-layer');
        if (pinLayer) {
            wrapper.insertBefore(layer, pinLayer);
        } else {
            wrapper.appendChild(layer);
        }
        page.getTextContent().then(function (textContent) {
            const task = window.pdfjsLib.renderTextLayer({
                textContentSource: textContent,
                textContent: textContent,
                container: layer,
                viewport: viewport,
                textDivs: [],
            });
            if (task && task.promise) {
                task.promise.catch(function () {});
            }
        }).catch(function () {});
    }

    function renderPages() {
        if (!pdfDocument) {
            return;
        }
        viewer.querySelectorAll('.pdf-page, .pdf-loading').forEach(function (node) {
            node.remove();
        });
        pages.clear();
        const sequence = [];
        for (let number = 1; number <= pdfDocument.numPages; number += 1) {
            sequence.push(number);
        }
        return sequence.reduce(function (chain, number) {
            return chain.then(function () {
                return pdfDocument.getPage(number).then(function (page) {
                    const viewport = page.getViewport({
                        scale: currentScale,
                        rotation: currentRotation,
                    });
                    const outputScale = window.devicePixelRatio || 1;
                    const wrapper = document.createElement('div');
                    wrapper.className = 'pdf-page';
                    wrapper.dataset.page = String(number);
                    wrapper.style.width = Math.floor(viewport.width) + 'px';
                    const canvas = document.createElement('canvas');
                    canvas.width = Math.floor(viewport.width * outputScale);
                    canvas.height = Math.floor(viewport.height * outputScale);
                    canvas.style.width = Math.floor(viewport.width) + 'px';
                    canvas.style.height = Math.floor(viewport.height) + 'px';
                    const context = canvas.getContext('2d');
                    context.setTransform(
                        outputScale, 0, 0, outputScale, 0, 0
                    );
                    const layer = document.createElement('div');
                    layer.className = 'pdf-pin-layer';
                    wrapper.appendChild(canvas);
                    wrapper.appendChild(layer);
                    viewer.appendChild(wrapper);
                    buildTextLayer(page, viewport, wrapper);
                    pages.set(number, {wrapper: wrapper, canvas: canvas, layer: layer});
                    bindPageInteraction(wrapper, canvas);
                    return page.render({
                        canvasContext: context,
                        viewport: viewport,
                    }).promise.then(function () {
                        drawMarks(number);
                    });
                });
            });
        }, Promise.resolve());
    }

    function toggleToolbarButton(action, active) {
        const toolbar = document.getElementById('pdfToolbar');
        if (!toolbar) {
            return;
        }
        const button = toolbar.querySelector('[data-pdf-action="' + action + '"]');
        if (button) {
            button.classList.toggle('active', active);
        }
    }

    function initToolbar() {
        const toolbar = document.getElementById('pdfToolbar');
        if (!toolbar) {
            return;
        }
        toolbar.addEventListener('click', function (event) {
            const button = event.target.closest('[data-pdf-action]');
            if (!button) {
                return;
            }
            const action = button.dataset.pdfAction;
            if (action === 'zoom-in' && currentScale < 3) {
                currentScale *= 1.2;
                renderPages();
            } else if (action === 'zoom-out' && currentScale > 0.5) {
                currentScale /= 1.2;
                renderPages();
            } else if (action === 'rotate') {
                currentRotation = (currentRotation - 90 + 360) % 360;
                renderPages();
            } else if (action === 'pan') {
                panMode = !panMode;
                textMode = false;
                viewer.classList.toggle('is-pan', panMode);
                viewer.classList.remove('text-mode');
                toggleToolbarButton('pan', panMode);
                toggleToolbarButton('text', false);
            } else if (action === 'text') {
                textMode = !textMode;
                panMode = false;
                viewer.classList.toggle('text-mode', textMode);
                viewer.classList.remove('is-pan');
                toggleToolbarButton('text', textMode);
                toggleToolbarButton('pan', false);
            }
        });

    }

    function initPan() {
        let isPanning = false;
        let startX = 0;
        let startY = 0;
        let scrollLeft = 0;
        let scrollTop = 0;

        viewer.addEventListener('mousedown', function (event) {
            if (!panMode || event.button !== 0) {
                return;
            }
            isPanning = true;
            startX = event.clientX;
            startY = event.clientY;
            scrollLeft = viewer.scrollLeft;
            scrollTop = viewer.scrollTop;
            viewer.classList.add('is-panning');
            viewer.style.cursor = 'grabbing';
            event.preventDefault();
        });

        window.addEventListener('mousemove', function (event) {
            if (!isPanning) {
                return;
            }
            event.preventDefault();
            const walkX = (event.clientX - startX) * 1.5;
            const walkY = (event.clientY - startY) * 1.5;
            viewer.scrollLeft = scrollLeft - walkX;
            viewer.scrollTop = scrollTop - walkY;
        });

        window.addEventListener('mouseup', function () {
            if (!isPanning) {
                return;
            }
            isPanning = false;
            viewer.classList.remove('is-panning');
            viewer.style.cursor = panMode ? 'grab' : 'default';
        });
    }

    function initContextMenu() {
        if (!contextMenu) {
            return;
        }
        contextMenu.addEventListener('click', function (event) {
            const statusButton = event.target.closest('.context-status');
            if (statusButton && details) {
                const formNode = document.createElement('form');
                formNode.method = 'post';
                formNode.action = urlFromTemplate(
                    details.dataset.statusUrlTemplate, contextIssueId
                );
                formNode.innerHTML = csrfInputHtml()
                    + '<input type="hidden" name="status" value="'
                    + escapeHtml(statusButton.dataset.status) + '">';
                document.body.appendChild(formNode);
                formNode.submit();
                return;
            }
            const actionButton = event.target.closest('[data-action]');
            if (!actionButton || contextIssueId == null) {
                return;
            }
            const issue = findIssue(contextIssueId);
            const action = actionButton.dataset.action;
            closeContextMenu();
            if (!issue) {
                return;
            }
            if (action === 'answer') {
                selectIssue(issue.id, {focusAnswer: true});
            } else if (action === 'copy-link') {
                const link = window.location.origin + window.location.pathname
                    + '#issue-' + issue.id;
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(link);
                }
            } else if (action === 'delete') {
                if (!window.confirm('Удалить замечание #' + issue.id + '?')) {
                    return;
                }
                fetch(urlFromTemplate(details.dataset.deleteUrlTemplate, issue.id), {
                    method: 'POST',
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest',
                        'X-CSRFToken': csrfToken(),
                    },
                    credentials: 'same-origin',
                }).then(function (response) {
                    if (!response.ok) {
                        throw new Error('Не удалось удалить замечание');
                    }
                    removeIssue(issue.id);
                }).catch(function (error) {
                    window.alert(error.message);
                });
            } else if (action === 'show-mark') {
                selectIssue(issue.id, {scrollMark: true});
            } else if (action === 'download') {
                if (issue.crop_image_url) {
                    window.open(issue.crop_image_url, '_blank');
                }
            }
        });

        document.addEventListener('click', function (event) {
            if (!event.target.closest('#issueContextMenu')) {
                closeContextMenu();
            }
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                closeContextMenu();
            }
        });
    }

    function csrfToken() {
        const input = form ? form.querySelector('input[name="csrfmiddlewaretoken"]') : null;
        return input ? input.value : '';
    }

    function removeIssue(issueId) {
        const index = issues.findIndex(function (issue) {
            return Number(issue.id) === Number(issueId);
        });
        if (index >= 0) {
            const issue = issues[index];
            issues.splice(index, 1);
            drawMarks(issue.page_number);
        }
        const card = document.querySelector('.issue-card[data-issue-id="' + issueId + '"]');
        if (card) {
            card.remove();
        }
        if (issuesCount) {
            issuesCount.textContent = String(issues.length);
        }
        if (selectedIssueId != null && Number(selectedIssueId) === Number(issueId)) {
            selectedIssueId = null;
            const element = document.getElementById('issueElement');
            const empty = document.getElementById('issueElementEmpty');
            if (element) {
                element.classList.add('d-none');
            }
            if (empty) {
                empty.classList.remove('d-none');
            }
        }
    }

    function initDetails() {
        const saveButton = document.getElementById('detailAnswerSave');
        const answerField = document.getElementById('detailAnswer');
        if (saveButton && answerField && details) {
            saveButton.addEventListener('click', function () {
                if (selectedIssueId == null) {
                    return;
                }
                const body = new FormData();
                body.append('answer_text', answerField.value);
                fetch(urlFromTemplate(details.dataset.answerUrlTemplate, selectedIssueId), {
                    method: 'POST',
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest',
                        'X-CSRFToken': csrfToken(),
                    },
                    body: body,
                    credentials: 'same-origin',
                }).then(function (response) {
                    return response.json().then(function (data) {
                        if (!response.ok) {
                            throw new Error(data.error || 'Не удалось сохранить ответ');
                        }
                        const issue = findIssue(selectedIssueId);
                        if (issue) {
                            issue.answer_text = data.answer_text;
                        }
                        saveButton.textContent = 'Сохранено';
                        window.setTimeout(function () {
                            saveButton.textContent = 'Сохранить ответ';
                        }, 1500);
                    });
                }).catch(function (error) {
                    window.alert(error.message);
                });
            });
        }
    }

    if (window.pdfjsLib && pdfUrl) {
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = viewer.dataset.workerUrl;
        window.pdfjsLib.getDocument(pdfUrl).promise.then(function (pdf) {
            pdfDocument = pdf;
            return renderPages();
        }).catch(function () {
            viewer.innerHTML = '<div class="alert alert-warning m-3 mb-0">Не удалось загрузить PDF.</div>';
        });
    }

    const newIssueBtn = document.getElementById('newIssueBtn');
    if (newIssueBtn) {
        newIssueBtn.addEventListener('click', function () {
            openModal(null, null, null, '');
        });
    }

    if (issuesList) {
        issuesList.addEventListener('click', function (event) {
            if (event.target.closest('form, button, a')) {
                return;
            }
            const card = event.target.closest('.issue-card');
            if (card) {
                selectIssue(card.dataset.issueId, {scrollMark: true});
            }
        });
        issuesList.addEventListener('contextmenu', function (event) {
            const card = event.target.closest('.issue-card');
            if (!card) {
                return;
            }
            event.preventDefault();
            openContextMenu(event.clientX, event.clientY, card.dataset.issueId);
        });
    }

    if (form) {
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            if (!createUrl) {
                return;
            }
            const formData = new FormData(form);
            formData.set('crop_image_base64', pendingCrop);
            fetch(createUrl, {
                method: 'POST',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRFToken': csrfToken(),
                },
                body: formData,
                credentials: 'same-origin',
            }).then(function (response) {
                return response.json().then(function (data) {
                    if (!response.ok) {
                        throw new Error(data.error || 'Не удалось создать замечание.');
                    }
                    return data;
                });
            }).then(function (data) {
                const issue = data.issue;
                issues.push(issue);
                if (issue.page_number) {
                    drawMarks(issue.page_number);
                    const page = pages.get(Number(issue.page_number));
                    if (page) {
                        page.wrapper.scrollIntoView({behavior: 'smooth', block: 'center'});
                    }
                }
                appendIssueCard(issue);
                selectIssue(issue.id);
                form.reset();
                pendingCrop = '';
                if (modal) {
                    modal.hide();
                }
            }).catch(function (error) {
                if (errorBox) {
                    errorBox.textContent = error.message;
                    errorBox.classList.remove('d-none');
                }
            });
        });
    }

    window.zoomIn = function () {
        currentScale *= 1.2;
        renderPages();
    };

    window.zoomOut = function () {
        currentScale /= 1.2;
        renderPages();
    };

    window.rotatePage = function () {
        currentRotation = (currentRotation - 90 + 360) % 360;
        renderPages();
    };

    window.togglePan = function () {
        panMode = !panMode;
        textMode = false;
        viewer.classList.toggle('is-pan', panMode);
        viewer.classList.remove('is-panning', 'text-mode');
        viewer.style.cursor = panMode ? 'grab' : 'default';
    };

    window.toggleSelect = function () {
        textMode = !textMode;
        panMode = false;
        viewer.classList.toggle('text-mode', textMode);
        viewer.classList.remove('is-pan', 'is-panning');
        viewer.style.cursor = 'default';
    };

    window.copyText = function () {
        if (navigator.clipboard) {
            navigator.clipboard.writeText(window.getSelection().toString());
        }
    };

    window.renderPage = renderPages;

    initToolbar();
    initPan();
    initContextMenu();
    initDetails();
    if (issues.length) {
        selectIssue(issues[0].id);
    }
})();
