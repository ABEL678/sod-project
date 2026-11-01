(function () {
    'use strict';

    window.zoomIn = window.zoomIn || function () {};
    window.zoomOut = window.zoomOut || function () {};
    window.rotatePage = window.rotatePage || function () {};
    window.togglePan = window.togglePan || function () {};
    window.toggleSelect = window.toggleSelect || function () {};
    window.copyText = window.copyText || function () {};
    window.toggleOverlay = window.toggleOverlay || function () {};

    const viewer = document.getElementById('pdfViewer');
    if (!viewer) {
        return;
    }

    const pdfUrl = viewer.dataset.pdfUrl;
    const pdfUrlCurrent = viewer.dataset.pdfUrlCurrent || '';
    const isCurrentView = viewer.dataset.isCurrent !== 'false';
    const createUrl = viewer.dataset.issueCreateUrl;
    const canCreate = viewer.dataset.canCreate === '1' && isCurrentView;
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

    // Цвета статусов приходят из дизайн-токенов (static/js/design-tokens.js,
    // генерируется из tokens.json). Фолбэк — только аварийный синий.
    const STATUS_COLORS = (window.CDE_TOKENS && window.CDE_TOKENS.statusHex) || {};
    const STATUS_FALLBACK_COLOR = '#0d6efd';

    const pages = new Map();
    const pageCleanups = [];
    let pdfDocument = null;
    let currentScale = 1.0;
    let currentRotation = 0;
    let currentPage = 1;
    let panMode = false;
    let textMode = false;
    let selectedIssueId = null;
    let contextIssueId = null;
    let pendingCrop = '';

    function statusColor(status) {
        return STATUS_COLORS[status] || STATUS_FALLBACK_COLOR;
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
        if (!isCurrentView) {
            return;
        }
        drawMarksInto(pages, pageNumber);
    }

    function drawMarksInto(pageMap, pageNumber) {
        const page = pageMap.get(Number(pageNumber));
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
                let x = issue.x_ratio;
                let y = issue.y_ratio;
                if (
                    x == null && y == null
                    && issue.x1_ratio != null && issue.y1_ratio != null
                    && issue.x2_ratio != null && issue.y2_ratio != null
                ) {
                    x = (issue.x1_ratio + issue.x2_ratio) / 2;
                    y = (issue.y1_ratio + issue.y2_ratio) / 2;
                }
                if (x == null || y == null) {
                    return;
                }

                let x1 = issue.x1_ratio;
                let y1 = issue.y1_ratio;
                let x2 = issue.x2_ratio;
                let y2 = issue.y2_ratio;
                if (x1 == null || y1 == null || x2 == null || y2 == null) {
                    x1 = Math.max(0, x - 0.02);
                    y1 = Math.max(0, y - 0.02);
                    x2 = Math.min(1, x + 0.02);
                    y2 = Math.min(1, y + 0.02);
                }

                const frame = document.createElement('div');
                frame.className = 'pdf-markup';
                frame.dataset.issueId = issue.id;
                frame.style.left = (x1 * 100) + '%';
                frame.style.top = (y1 * 100) + '%';
                frame.style.width = ((x2 - x1) * 100) + '%';
                frame.style.height = ((y2 - y1) * 100) + '%';
                frame.style.borderColor = statusColor(issue.status);
                frame.style.backgroundColor = 'transparent';
                frame.style.pointerEvents = 'auto';
                frame.style.cursor = 'pointer';
                frame.title = issue.description;
                frame.addEventListener('click', function (event) {
                    event.stopPropagation();
                    if (window.selectIssueFromPdf) {
                        window.selectIssueFromPdf(issue.id);
                    }
                });
                frame.addEventListener('contextmenu', function (event) {
                    event.preventDefault();
                    event.stopPropagation();
                    openContextMenu(event.clientX, event.clientY, issue.id);
                });
                page.layer.appendChild(frame);

                const badge = document.createElement('span');
                const badgeColor = statusColor(issue.status);
                badge.className = 'pdf-issue-badge';
                badge.dataset.issueId = issue.id;
                badge.style.position = 'absolute';
                badge.style.top = '-12px';
                badge.style.left = '-12px';
                badge.style.transform = 'none';
                badge.style.backgroundColor = badgeColor;
                badge.style.color = ['IN_PROGRESS', 'DISCUSSION'].includes(issue.status)
                    ? '#212529'
                    : '#fff';
                badge.style.border = '2px solid #fff';
                badge.style.borderRadius = '50%';
                badge.style.padding = '0';
                badge.style.fontSize = '10px';
                badge.style.fontWeight = 'bold';
                badge.style.lineHeight = '1';
                badge.style.width = '24px';
                badge.style.height = '16px';
                badge.style.display = 'flex';
                badge.style.alignItems = 'center';
                badge.style.justifyContent = 'center';
                badge.style.textAlign = 'center';
                badge.style.boxShadow = '0 2px 4px rgba(0,0,0,0.2)';
                badge.style.pointerEvents = 'none';
                badge.style.zIndex = '2';
                badge.textContent = issue.id;
                badge.title = issue.description;
                frame.appendChild(badge);
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
            preview.style.borderColor = statusColor('NEW');
            preview.style.backgroundColor = withAlpha(statusColor('NEW'), 0.1);
            wrapper.querySelector('.pdf-pin-layer').appendChild(preview);
            event.preventDefault();
        });

        function handleMove(event) {
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
        }

        function handleUp(event) {
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
        }

        window.addEventListener('mousemove', handleMove);
        window.addEventListener('mouseup', handleUp);
        pageCleanups.push(function () {
            window.removeEventListener('mousemove', handleMove);
            window.removeEventListener('mouseup', handleUp);
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

    function updatePageControls() {
        const total = pdfDocument ? pdfDocument.numPages : 1;
        currentPage = Math.min(Math.max(1, Number(currentPage) || 1), total);
        const input = document.getElementById('pageInput');
        if (input) {
            input.max = String(total);
            input.value = String(currentPage);
        }
        const totalLabel = document.getElementById('totalPages');
        if (totalLabel) {
            totalLabel.textContent = String(total);
        }
        updateMinimapLabel();
    }

    function renderPage(pageNumber) {
        if (!pdfDocument) {
            return;
        }
        const total = pdfDocument.numPages;
        currentPage = Math.min(
            Math.max(1, Number(pageNumber) || 1),
            total
        );

        pageCleanups.splice(0).forEach(function (cleanup) {
            cleanup();
        });
        viewer.querySelectorAll('.pdf-page, .pdf-loading').forEach(function (node) {
            node.remove();
        });
        pages.clear();

        return pdfDocument.getPage(currentPage).then(function (page) {
            const viewport = page.getViewport({
                scale: currentScale,
                rotation: currentRotation,
            });
            const outputScale = window.devicePixelRatio || 1;
            const wrapper = document.createElement('div');
            wrapper.className = 'pdf-page';
            wrapper.dataset.page = String(currentPage);
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
            pages.set(currentPage, {
                wrapper: wrapper,
                canvas: canvas,
                layer: layer,
            });
            bindPageInteraction(wrapper, canvas);
            updatePageControls();
            return page.render({
                canvasContext: context,
                viewport: viewport,
            }).promise.then(function () {
                drawMarks(currentPage);
                renderMinimap();
            });
        });
    }

    function renderPages() {
        return renderPage(currentPage);
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
            if (action === 'zoom-in' && currentScale < 4) {
                window.zoomIn();
            } else if (action === 'zoom-out' && currentScale > 0.5) {
                window.zoomOut();
            } else if (action === 'rotate') {
                window.rotatePage();
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
        const panContainer = viewer.closest('.stage') || viewer;

        viewer.style.overflow = 'visible';

        panContainer.addEventListener('mousedown', function (event) {
            if (!panMode || event.button !== 0) {
                return;
            }
            if (comparison.active && comparison.mode !== 'slider') {
                return;
            }
            if (event.target.closest('#sliderDivider')) {
                return;
            }
            isPanning = true;
            startX = event.clientX;
            startY = event.clientY;
            scrollLeft = panContainer.scrollLeft;
            scrollTop = panContainer.scrollTop;
            viewer.classList.add('is-panning');
            panContainer.classList.add('is-panning');
            viewer.style.cursor = 'grabbing';
            panContainer.style.cursor = 'grabbing';
            event.preventDefault();
        });

        window.addEventListener('mousemove', function (event) {
            if (!isPanning) {
                return;
            }
            event.preventDefault();
            const walkX = (event.clientX - startX) * 1.5;
            const walkY = (event.clientY - startY) * 1.5;
            panContainer.scrollLeft = scrollLeft - walkX;
            panContainer.scrollTop = scrollTop - walkY;
        });

        window.addEventListener('mouseup', function () {
            if (!isPanning) {
                return;
            }
            isPanning = false;
            viewer.classList.remove('is-panning');
            panContainer.classList.remove('is-panning');
            viewer.style.cursor = panMode ? 'grab' : 'default';
            panContainer.style.cursor = panMode ? 'grab' : 'default';
        });
    }

    function initSlider() {
        const divider = document.getElementById('sliderDivider');
        const frame = document.getElementById('sliderFrame');
        if (!divider || !frame) {
            return;
        }
        let dragging = false;

        divider.addEventListener('mousedown', function (event) {
            if (!comparison.active || comparison.mode !== 'slider' || event.button !== 0) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            dragging = true;
            document.body.style.userSelect = 'none';
            document.body.style.cursor = 'col-resize';
        });

        divider.addEventListener('keydown', function (event) {
            if (!comparison.active || comparison.mode !== 'slider') {
                return;
            }
            if (event.key === 'ArrowLeft') {
                event.preventDefault();
                setSliderSplit(comparison.split - 2);
            } else if (event.key === 'ArrowRight') {
                event.preventDefault();
                setSliderSplit(comparison.split + 2);
            }
        });

        window.addEventListener('mousemove', function (event) {
            if (!dragging) {
                return;
            }
            const rect = frame.getBoundingClientRect();
            if (!rect.width) {
                return;
            }
            setSliderSplit(((event.clientX - rect.left) / rect.width) * 100);
        });

        window.addEventListener('mouseup', function () {
            if (!dragging) {
                return;
            }
            dragging = false;
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
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

    const comparison = {
        active: false,
        mode: null,
        selected: null,
        current: null,
        page: 1,
        scale: 1,
        rotation: 0,
        split: 50,
        loading: false,
        geometryMismatch: false,
        requestId: 0,
    };
    let comparisonScrollLock = false;

    function comparisonPane(viewerElement, url, drawIssueMarks) {
        return {
            viewer: viewerElement,
            url: url,
            drawIssueMarks: drawIssueMarks,
            pdfDocument: null,
            pageMap: new Map(),
            pageNumber: 1,
            viewport: null,
            renderTask: null,
            renderToken: 0,
        };
    }

    function comparisonTotalPages() {
        if (comparison.current && comparison.current.pdfDocument) {
            return comparison.current.pdfDocument.numPages;
        }
        if (comparison.selected && comparison.selected.pdfDocument) {
            return comparison.selected.pdfDocument.numPages;
        }
        return 1;
    }

    function updateComparisonPageControls() {
        const total = comparisonTotalPages();
        const input = document.getElementById('pageInput');
        if (input) {
            input.max = String(total);
            input.value = String(comparison.page);
        }
        const totalLabel = document.getElementById('totalPages');
        if (totalLabel) {
            totalLabel.textContent = String(total);
        }
    }

    function updateComparisonButton() {
        const button = document.getElementById('compareToggleBtn');
        if (!button) {
            return;
        }
        const sideActive = comparison.active && comparison.mode === 'side';
        button.textContent = sideActive
            ? 'Закрыть сравнение'
            : 'Сравнить с текущей';
        button.disabled = !sideActive && (isCurrentView || !pdfUrlCurrent);
    }

    function updateSliderButton() {
        const button = document.getElementById('sliderToggleBtn');
        if (!button) {
            return;
        }
        const label = document.getElementById('sliderToggleLabel');
        const sliderActive = comparison.active && comparison.mode === 'slider';
        if (sliderActive) {
            if (label) {
                label.textContent = 'Закрыть сравнение';
            }
            button.disabled = false;
            button.title = 'Закрыть шторку';
            return;
        }
        if (label) {
            label.textContent = 'Шторка';
        }
        button.disabled = isCurrentView
            || !pdfUrl
            || !pdfUrlCurrent
            || comparison.geometryMismatch
            || comparison.loading;
        if (comparison.geometryMismatch) {
            button.title = 'Шторка доступна только для листов с одинаковой геометрией. Используйте режим «Рядом».';
        } else if (isCurrentView) {
            button.title = 'Доступно только при просмотре не-current версии';
        } else if (comparison.loading) {
            button.title = 'Проверка геометрии…';
        } else {
            button.title = 'Сравнить версии шторкой';
        }
    }

    function loadComparisonPane(pane) {
        pane.viewer.innerHTML =
            '<div class="pdf-loading text-muted small p-3">Загрузка PDF…</div>';
        return window.pdfjsLib.getDocument(pane.url).promise.then(function (pdf) {
            pane.pdfDocument = pdf;
            return pdf;
        }).catch(function () {
            pane.viewer.innerHTML =
                '<div class="alert alert-warning m-3 mb-0">'
                + 'Не удалось загрузить PDF.</div>';
        });
    }

    function renderComparisonPane(pane, pageNumber) {
        if (!pane || !pane.pdfDocument) {
            return Promise.resolve();
        }
        const total = pane.pdfDocument.numPages;
        const target = Math.min(Math.max(1, Number(pageNumber) || 1), total);
        pane.pageNumber = target;
        const token = pane.renderToken + 1;
        pane.renderToken = token;
        if (pane.renderTask) {
            try {
                pane.renderTask.cancel();
            } catch (error) {
                pane.renderTask = null;
            }
        }
        pane.renderTask = null;
        pane.viewer.querySelectorAll('.pdf-page, .pdf-loading').forEach(function (node) {
            node.remove();
        });
        pane.pageMap.clear();

        return pane.pdfDocument.getPage(target).then(function (page) {
            if (token !== pane.renderToken) {
                return;
            }
            const viewport = page.getViewport({
                scale: comparison.scale,
                rotation: comparison.rotation,
            });
            pane.viewport = {
                width: viewport.width,
                height: viewport.height,
            };
            const outputScale = window.devicePixelRatio || 1;
            const wrapper = document.createElement('div');
            wrapper.className = 'pdf-page';
            wrapper.dataset.page = String(target);
            wrapper.style.width = Math.floor(viewport.width) + 'px';
            const canvas = document.createElement('canvas');
            canvas.width = Math.floor(viewport.width * outputScale);
            canvas.height = Math.floor(viewport.height * outputScale);
            canvas.style.width = Math.floor(viewport.width) + 'px';
            canvas.style.height = Math.floor(viewport.height) + 'px';
            const context = canvas.getContext('2d');
            context.setTransform(outputScale, 0, 0, outputScale, 0, 0);
            const layer = document.createElement('div');
            layer.className = 'pdf-pin-layer';
            wrapper.appendChild(canvas);
            wrapper.appendChild(layer);
            pane.viewer.appendChild(wrapper);
            pane.pageMap.set(target, {
                wrapper: wrapper,
                canvas: canvas,
                layer: layer,
            });
            const task = page.render({
                canvasContext: context,
                viewport: viewport,
            });
            pane.renderTask = task;
            return task.promise.then(function () {
                if (token !== pane.renderToken) {
                    return;
                }
                if (pane.drawIssueMarks) {
                    drawMarksInto(pane.pageMap, target);
                }
            }).catch(function () {});
        });
    }

    function syncComparisonScroll(source) {
        if (!comparison.active || comparisonScrollLock) {
            return;
        }
        const other = source === comparison.selected
            ? comparison.current
            : comparison.selected;
        if (!other) {
            return;
        }
        comparisonScrollLock = true;
        const sourceMaxY = Math.max(
            1, source.viewer.scrollHeight - source.viewer.clientHeight
        );
        const sourceMaxX = Math.max(
            1, source.viewer.scrollWidth - source.viewer.clientWidth
        );
        const ratioY = source.viewer.scrollTop / sourceMaxY;
        const ratioX = source.viewer.scrollLeft / sourceMaxX;
        other.viewer.scrollTop = ratioY * Math.max(
            0, other.viewer.scrollHeight - other.viewer.clientHeight
        );
        other.viewer.scrollLeft = ratioX * Math.max(
            0, other.viewer.scrollWidth - other.viewer.clientWidth
        );
        window.requestAnimationFrame(function () {
            comparisonScrollLock = false;
        });
    }

    function comparisonPaneForViewer(element) {
        if (comparison.selected && comparison.selected.viewer === element) {
            return comparison.selected;
        }
        if (comparison.current && comparison.current.viewer === element) {
            return comparison.current;
        }
        return null;
    }

    function bindComparisonPane(pane) {
        if (pane.viewer.dataset.compareBound === '1') {
            return;
        }
        pane.viewer.dataset.compareBound = '1';
        const element = pane.viewer;
        element.addEventListener('scroll', function () {
            const source = comparisonPaneForViewer(element);
            if (source) {
                syncComparisonScroll(source);
            }
        });
        element.addEventListener('wheel', function (event) {
            if (!event.ctrlKey) {
                return;
            }
            event.preventDefault();
            if (event.deltaY < 0) {
                window.zoomIn();
            } else {
                window.zoomOut();
            }
        }, {passive: false});
    }

    function updateSliderFrame() {
        const frame = document.getElementById('sliderFrame');
        const pane = comparison.current || comparison.selected;
        if (!frame || !pane || !pane.viewport) {
            return;
        }
        frame.style.width = Math.ceil(pane.viewport.width) + 'px';
        frame.style.height = Math.ceil(pane.viewport.height) + 'px';
        frame.style.setProperty('--split', comparison.split + '%');
    }

    function setSliderSplit(percent) {
        let value = Number(percent);
        if (!isFinite(value)) {
            value = 50;
        }
        value = Math.min(96, Math.max(4, value));
        comparison.split = value;
        const frame = document.getElementById('sliderFrame');
        if (frame) {
            frame.style.setProperty('--split', value + '%');
        }
        const divider = document.getElementById('sliderDivider');
        if (divider) {
            divider.setAttribute('aria-valuenow', String(Math.round(value)));
        }
    }

    function renderComparisonPage(pageNumber) {
        if (!comparison.active) {
            return Promise.resolve();
        }
        const total = comparisonTotalPages();
        comparison.page = Math.min(
            Math.max(1, Number(pageNumber) || 1),
            total
        );
        updateComparisonPageControls();
        return Promise.all([
            renderComparisonPane(comparison.selected, comparison.page),
            renderComparisonPane(comparison.current, comparison.page),
        ]).then(function () {
            if (comparison.mode === 'slider') {
                updateSliderFrame();
            }
        });
    }

    function startComparison(mode, selectedDocument, currentDocument) {
        if (isCurrentView || !pdfUrl || !pdfUrlCurrent) {
            return null;
        }
        const compareStage = document.getElementById('compareStage');
        const sliderStage = document.getElementById('sliderStage');
        const stage = document.getElementById('pdf-panel');
        const selectedViewer = document.getElementById(
            mode === 'side' ? 'compareViewerSelected' : 'sliderViewerSelected'
        );
        const currentViewer = document.getElementById(
            mode === 'side' ? 'compareViewerCurrent' : 'sliderViewerCurrent'
        );
        if (!compareStage || !sliderStage || !selectedViewer || !currentViewer) {
            return null;
        }
        if (comparison.active) {
            closeComparison();
        }

        comparison.active = true;
        comparison.mode = mode;
        comparison.page = currentPage;
        comparison.scale = currentScale;
        comparison.rotation = currentRotation;
        comparison.selected = comparisonPane(selectedViewer, pdfUrl, false);
        comparison.current = comparisonPane(currentViewer, pdfUrlCurrent, true);
        comparison.selected.pdfDocument = selectedDocument || null;
        comparison.current.pdfDocument = currentDocument || null;
        bindComparisonPane(comparison.selected);
        bindComparisonPane(comparison.current);

        if (mode === 'slider') {
            setSliderSplit(50);
        }

        compareStage.classList.toggle('d-none', mode !== 'side');
        sliderStage.classList.toggle('d-none', mode !== 'slider');
        viewer.classList.add('d-none');
        setFitButtonsEnabled(false);
        if (stage) {
            stage.classList.toggle('is-comparing', mode === 'side');
            stage.classList.toggle('is-sliding', mode === 'slider');
        }
        updateComparisonButton();
        updateSliderButton();
        updateComparisonPageControls();

        const loads = [];
        if (!comparison.selected.pdfDocument) {
            loads.push(loadComparisonPane(comparison.selected));
        }
        if (!comparison.current.pdfDocument) {
            loads.push(loadComparisonPane(comparison.current));
        }
        return Promise.all(loads).then(function () {
            if (!comparison.active || comparison.mode !== mode) {
                return null;
            }
            return renderComparisonPage(comparison.page);
        });
    }

    function openComparison() {
        if (comparison.active && comparison.mode === 'side') {
            closeComparison();
            return;
        }
        if (isCurrentView || !pdfUrl || !pdfUrlCurrent) {
            return;
        }
        if (comparison.active) {
            closeComparison();
        }
        comparison.requestId += 1;
        comparison.loading = false;
        updateSliderButton();
        startComparison('side');
    }

    function openSliderComparison() {
        if (isCurrentView || !pdfUrl || !pdfUrlCurrent) {
            return;
        }
        if (comparison.active && comparison.mode === 'slider') {
            closeComparison();
            return;
        }
        if (comparison.loading) {
            return;
        }

        const requestId = comparison.requestId + 1;
        comparison.requestId = requestId;
        comparison.loading = true;
        updateSliderButton();

        Promise.all([
            window.pdfjsLib.getDocument(pdfUrl).promise,
            window.pdfjsLib.getDocument(pdfUrlCurrent).promise,
        ]).then(function (documents) {
            if (requestId !== comparison.requestId) {
                return null;
            }
            return Promise.all([
                documents[0].getPage(1),
                documents[1].getPage(1),
            ]).then(function (pages) {
                if (requestId !== comparison.requestId) {
                    return null;
                }
                const selectedViewport = pages[0].getViewport({scale: 1});
                const currentViewport = pages[1].getViewport({scale: 1});
                if (
                    Math.abs(selectedViewport.width - currentViewport.width) >= 1
                    || Math.abs(selectedViewport.height - currentViewport.height) >= 1
                ) {
                    comparison.geometryMismatch = true;
                    comparison.loading = false;
                    updateSliderButton();
                    window.alert(
                        'Шторка доступна только для листов с одинаковой геометрией. Используйте режим «Рядом».'
                    );
                    return null;
                }
                comparison.geometryMismatch = false;
                comparison.loading = false;
                if (comparison.active && comparison.mode === 'side') {
                    closeComparison();
                }
                startComparison('slider', documents[0], documents[1]);
                return null;
            });
        }).catch(function () {
            if (requestId !== comparison.requestId) {
                return;
            }
            comparison.loading = false;
            updateSliderButton();
            window.alert('Не удалось загрузить PDF для сравнения.');
        });
    }

    function closeComparison() {
        if (!comparison.active) {
            return;
        }
        const page = comparison.page;
        comparison.active = false;
        comparison.mode = null;
        comparison.requestId += 1;
        comparison.loading = false;
        comparison.split = 50;
        [comparison.selected, comparison.current].forEach(function (pane) {
            if (!pane) {
                return;
            }
            if (pane.renderTask) {
                try {
                    pane.renderTask.cancel();
                } catch (error) {
                    pane.renderTask = null;
                }
            }
            pane.viewer.innerHTML = '';
            pane.pageMap.clear();
            pane.pdfDocument = null;
        });
        comparison.selected = null;
        comparison.current = null;

        const compareStage = document.getElementById('compareStage');
        const sliderStage = document.getElementById('sliderStage');
        const stage = document.getElementById('pdf-panel');
        if (compareStage) {
            compareStage.classList.add('d-none');
        }
        if (sliderStage) {
            sliderStage.classList.add('d-none');
        }
        if (stage) {
            stage.classList.remove('is-comparing');
            stage.classList.remove('is-sliding');
        }
        viewer.classList.remove('d-none');
        setFitButtonsEnabled(true);
        updateComparisonButton();
        updateSliderButton();

        if (pdfDocument) {
            currentPage = page;
            renderPages();
        } else {
            updatePageControls();
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

    /* ====================================================================
       Мини-карта: живое превью текущего листа + рамка видимой области.
       В отличие от старой (удаляли её в 4d3620b — статичные SVG-заглушки
       «01/02»), здесь настоящий рендер страницы в маленьком масштабе:
       видно, где находишься на листе A1; клик/протяжка по мини-карте
       перемещает вьюпорт. Плавающая — не отнимает ширину у панели.
       ==================================================================== */
    const MINIMAP_WIDTH = 168;
    let minimapBox = null;
    let minimapView = null;
    let minimapCanvas = null;
    let minimapRect = null;
    let minimapLabel = null;
    let minimapVisible = true;

    function minimapStage() {
        return viewer.closest('.stage') || viewer;
    }

    function updateMinimapLabel() {
        if (minimapLabel && pdfDocument) {
            minimapLabel.textContent = 'Лист ' + currentPage + ' / ' + pdfDocument.numPages;
        }
    }

    function initMinimap() {
        const stage = minimapStage();
        if (!stage) {
            return;
        }
        minimapBox = document.createElement('div');
        minimapBox.className = 'pdf-minimap';
        minimapBox.id = 'pdfMinimap';
        minimapLabel = document.createElement('div');
        minimapLabel.className = 'pdf-minimap-label num';
        minimapView = document.createElement('div');
        minimapView.className = 'pdf-minimap-view';
        minimapCanvas = document.createElement('canvas');
        minimapRect = document.createElement('div');
        minimapRect.className = 'pdf-minimap-rect';
        minimapView.appendChild(minimapCanvas);
        minimapView.appendChild(minimapRect);
        minimapBox.appendChild(minimapLabel);
        minimapBox.appendChild(minimapView);
        stage.appendChild(minimapBox);

        try {
            minimapVisible = window.localStorage.getItem('cde-minimap') !== 'off';
        } catch (error) {
            minimapVisible = true;
        }
        minimapBox.classList.toggle('is-hidden', !minimapVisible);
        const toggle = document.getElementById('minimapToggle');
        if (toggle) {
            toggle.classList.toggle('active', minimapVisible);
        }

        minimapBox.addEventListener('mousedown', function (event) {
            if (event.button !== 0) {
                return;
            }
            event.preventDefault();
            minimapNavigate(event);
            const onMove = function (moveEvent) {
                minimapNavigate(moveEvent);
            };
            const onUp = function () {
                window.removeEventListener('mousemove', onMove);
                window.removeEventListener('mouseup', onUp);
            };
            window.addEventListener('mousemove', onMove);
            window.addEventListener('mouseup', onUp);
        });

        stage.addEventListener('scroll', function () {
            window.requestAnimationFrame(updateMinimapRect);
        }, { passive: true });
        window.addEventListener('resize', updateMinimapRect);
    }

    function minimapNavigate(event) {
        const stage = minimapStage();
        const page = pages.get(currentPage);
        if (!stage || !page || !minimapView) {
            return;
        }
        const viewRect = minimapView.getBoundingClientRect();
        if (!viewRect.width || !viewRect.height) {
            return;
        }
        const ratioX = Math.min(1, Math.max(0, (event.clientX - viewRect.left) / viewRect.width));
        const ratioY = Math.min(1, Math.max(0, (event.clientY - viewRect.top) / viewRect.height));
        const pageRect = page.wrapper.getBoundingClientRect();
        const stageRect = stage.getBoundingClientRect();
        // Хотим, чтобы точка ratio листа оказалась в той же доле вьюпорта.
        const targetX = ratioX * pageRect.width;
        const targetY = ratioY * pageRect.height;
        const currentX = (stageRect.left - pageRect.left) + stageRect.width * ratioX;
        const currentY = (stageRect.top - pageRect.top) + stageRect.height * ratioY;
        stage.scrollLeft += targetX - currentX;
        stage.scrollTop += targetY - currentY;
        updateMinimapRect();
    }

    function renderMinimap() {
        if (!minimapVisible || !pdfDocument || !minimapCanvas) {
            return;
        }
        pdfDocument.getPage(currentPage).then(function (page) {
            const base = page.getViewport({ scale: 1, rotation: currentRotation });
            if (!base.width) {
                return null;
            }
            const scale = MINIMAP_WIDTH / base.width;
            const viewport = page.getViewport({ scale: scale, rotation: currentRotation });
            const outputScale = window.devicePixelRatio || 1;
            minimapCanvas.width = Math.floor(viewport.width * outputScale);
            minimapCanvas.height = Math.floor(viewport.height * outputScale);
            minimapCanvas.style.width = Math.floor(viewport.width) + 'px';
            minimapCanvas.style.height = Math.floor(viewport.height) + 'px';
            const context = minimapCanvas.getContext('2d');
            context.setTransform(outputScale, 0, 0, outputScale, 0, 0);
            updateMinimapLabel();
            return page.render({
                canvasContext: context,
                viewport: viewport,
            }).promise;
        }).then(function () {
            updateMinimapRect();
        }).catch(function () {});
    }

    function updateMinimapRect() {
        if (!minimapVisible || !minimapRect || !minimapCanvas) {
            return;
        }
        const stage = minimapStage();
        const page = pages.get(currentPage);
        if (!stage || !page) {
            return;
        }
        const pageRect = page.wrapper.getBoundingClientRect();
        if (!pageRect.width || !pageRect.height) {
            return;
        }
        const stageRect = stage.getBoundingClientRect();
        // Видимая область в координатах листа (с обрезкой по границам).
        const left = Math.max(0, stageRect.left - pageRect.left);
        const top = Math.max(0, stageRect.top - pageRect.top);
        const right = Math.min(pageRect.width, stageRect.left + stageRect.width - pageRect.left);
        const bottom = Math.min(pageRect.height, stageRect.top + stageRect.height - pageRect.top);
        const scaleX = minimapCanvas.clientWidth / pageRect.width;
        const scaleY = minimapCanvas.clientHeight / pageRect.height;
        minimapRect.style.left = (left * scaleX) + 'px';
        minimapRect.style.top = (top * scaleY) + 'px';
        minimapRect.style.width = Math.max(6, (right - left) * scaleX) + 'px';
        minimapRect.style.height = Math.max(6, (bottom - top) * scaleY) + 'px';
    }

    window.toggleMinimap = function () {
        if (!minimapBox) {
            return;
        }
        minimapVisible = !minimapVisible;
        minimapBox.classList.toggle('is-hidden', !minimapVisible);
        try {
            window.localStorage.setItem('cde-minimap', minimapVisible ? 'on' : 'off');
        } catch (error) {
            /* приватный режим — просто не запоминаем */
        }
        const toggle = document.getElementById('minimapToggle');
        if (toggle) {
            toggle.classList.toggle('active', minimapVisible);
        }
        if (minimapVisible) {
            renderMinimap();
        }
    };

    window.goToPage = function (pageNumber) {
        if (comparison.active) {
            return renderComparisonPage(pageNumber);
        }
        return renderPage(pageNumber);
    };

    window.prevPage = function () {
        if (comparison.active) {
            if (comparison.page > 1) {
                renderComparisonPage(comparison.page - 1);
            }
            return;
        }
        if (pdfDocument && currentPage > 1) {
            renderPage(currentPage - 1);
        }
    };

    window.nextPage = function () {
        if (comparison.active) {
            if (comparison.page < comparisonTotalPages()) {
                renderComparisonPage(comparison.page + 1);
            }
            return;
        }
        if (pdfDocument && currentPage < pdfDocument.numPages) {
            renderPage(currentPage + 1);
        }
    };

    viewer.addEventListener('wheel', function (event) {
        if (!event.ctrlKey) {
            return;
        }
        event.preventDefault();
        if (event.deltaY < 0) {
            window.zoomIn();
        } else {
            window.zoomOut();
        }
    }, {passive: false});

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

    const ZOOM_OPTIONS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];

    window.zoomIn = function () {
        const next = ZOOM_OPTIONS.find(function (value) {
            return value > currentScale + 0.001;
        });
        window.setZoom(next || ZOOM_OPTIONS[ZOOM_OPTIONS.length - 1]);
    };

    window.zoomOut = function () {
        const previous = ZOOM_OPTIONS
            .slice()
            .reverse()
            .find(function (value) {
                return value < currentScale - 0.001;
            });
        window.setZoom(previous || ZOOM_OPTIONS[0]);
    };

    window.rotatePage = function () {
        currentRotation = (currentRotation - 90 + 360) % 360;
        if (comparison.active) {
            comparison.rotation = currentRotation;
            renderComparisonPage(comparison.page);
            return;
        }
        renderPages();
    };

    window.setZoom = function (scale) {
        scale = Number(scale);
        if (!isFinite(scale)) {
            scale = 1;
        }
        scale = Math.min(4, Math.max(0.5, scale));
        currentScale = scale;
        const button = document.getElementById('zoom-btn')
            || document.getElementById('zoom-level-btn')
            || document.querySelector('#pdf-panel .dropdown-toggle');
        if (button) {
            button.innerText = Math.round(scale * 100) + '%';
        }
        const label = document.getElementById('zoomLabel');
        if (label) {
            label.textContent = Math.round(scale * 100) + '%';
        }
        const select = document.getElementById('zoomSelect');
        if (select) {
            select.value = String(Math.round(scale * 100));
        }
        if (comparison.active) {
            comparison.scale = scale;
            renderComparisonPage(comparison.page);
            return;
        }
        renderPages();
    };

    function fitAvailableSize() {
        const container = viewer.closest('.stage') || viewer;
        const containerStyle = window.getComputedStyle(container);
        const viewerStyle = window.getComputedStyle(viewer);
        const paddingX = parseFloat(containerStyle.paddingLeft)
            + parseFloat(containerStyle.paddingRight)
            + parseFloat(viewerStyle.paddingLeft)
            + parseFloat(viewerStyle.paddingRight);
        const paddingY = parseFloat(containerStyle.paddingTop)
            + parseFloat(containerStyle.paddingBottom)
            + parseFloat(viewerStyle.paddingTop)
            + parseFloat(viewerStyle.paddingBottom);
        return {
            width: Math.max(200, container.clientWidth - paddingX),
            height: Math.max(200, container.clientHeight - paddingY),
        };
    }

    function setFitButtonsEnabled(enabled) {
        ['fitWidthBtn', 'fitPageBtn'].forEach(function (id) {
            const button = document.getElementById(id);
            if (button) {
                button.disabled = !enabled;
            }
        });
    }

    window.fitWidth = function () {
        if (!pdfDocument || comparison.active) {
            return;
        }
        pdfDocument.getPage(currentPage).then(function (page) {
            const base = page.getViewport({
                scale: 1,
                rotation: currentRotation,
            });
            const available = fitAvailableSize();
            window.setZoom(available.width / base.width);
        });
    };

    window.fitPage = function () {
        if (!pdfDocument || comparison.active) {
            return;
        }
        pdfDocument.getPage(currentPage).then(function (page) {
            const base = page.getViewport({
                scale: 1,
                rotation: currentRotation,
            });
            const available = fitAvailableSize();
            window.setZoom(Math.min(
                available.width / base.width,
                available.height / base.height
            ));
        });
    };

    // Обратная совместимость: старое имя работало как «по ширине».
    window.fitToPage = window.fitWidth;

    window.togglePan = function () {
        panMode = !panMode;
        textMode = false;
        viewer.classList.toggle('is-pan', panMode);
        viewer.classList.remove('is-panning', 'text-mode');
        viewer.style.cursor = panMode ? 'grab' : 'default';
        const panContainer = viewer.closest('.stage') || viewer;
        panContainer.style.cursor = panMode ? 'grab' : 'default';
    };

    window.toggleSelect = function () {
        textMode = !textMode;
        panMode = false;
        viewer.classList.toggle('text-mode', textMode);
        viewer.classList.remove('is-pan', 'is-panning');
        viewer.style.cursor = 'default';
        const panContainer = viewer.closest('.stage') || viewer;
        panContainer.style.cursor = 'default';
    };

    window.copyText = function () {
        if (navigator.clipboard) {
            navigator.clipboard.writeText(window.getSelection().toString());
        }
    };

    window.toggleOverlay = function () {
        alert('Режим наложения версий будет доступен в следующем обновлении.');
    };

    window.renderPage = renderPage;
    window.getCurrentPage = function () {
        return comparison.active ? comparison.page : currentPage;
    };
    window.closePdfComparison = closeComparison;

    const compareToggleBtn = document.getElementById('compareToggleBtn');
    if (compareToggleBtn) {
        compareToggleBtn.addEventListener('click', openComparison);
    }
    const sliderToggleBtn = document.getElementById('sliderToggleBtn');
    if (sliderToggleBtn) {
        sliderToggleBtn.addEventListener('click', openSliderComparison);
    }
    updateComparisonButton();
    updateSliderButton();

    // Смена статуса без перезагрузки: обновляем внутренний список и маркеры на листе.
    document.addEventListener('issue:status-changed', function (event) {
        const target = issues.find(function (item) {
            return item.id === Number(event.detail.id);
        });
        if (target) {
            target.status = event.detail.status;
        }
        if (comparison.active && comparison.current) {
            drawMarksInto(
                comparison.current.pageMap,
                comparison.current.pageNumber
            );
        } else {
            drawMarks(currentPage);
        }
    });

    initToolbar();
    initPan();
    initSlider();
    initContextMenu();
    initDetails();
    initMinimap();
    if (issues.length) {
        selectIssue(issues[0].id);
    }
})();
