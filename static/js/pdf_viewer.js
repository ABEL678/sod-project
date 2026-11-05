(function () {
    'use strict';

    window.zoomIn = window.zoomIn || function () {};
    window.zoomOut = window.zoomOut || function () {};
    window.rotatePage = window.rotatePage || function () {};
    window.togglePan = window.togglePan || function () {};
    window.toggleSelect = window.toggleSelect || function () {};
    window.copyText = window.copyText || function () {};
    /* Спринт 3: кнопка-заглушка toggleOverlay удалена — режим наложения
       это «Шторка» (T65), отдельной несуществующей функции больше нет. */

    /* Спринт 6: тост вместо блокирующего alert() */
    function viewerNotify(message) {
        if (typeof window.showToast === 'function') {
            window.showToast(message);
            return;
        }
        let toast = document.getElementById('viewerToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'viewerToast';
            toast.className = 'viewer-toast';
            toast.setAttribute('role', 'status');
            toast.setAttribute('aria-live', 'polite');
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.classList.add('is-visible');
        clearTimeout(viewerNotify._timer);
        viewerNotify._timer = setTimeout(function () {
            toast.classList.remove('is-visible');
        }, 3500);
    }

    const viewer = document.getElementById('pdfViewer');
    if (!viewer) {
        return;
    }

    const pdfUrl = viewer.dataset.pdfUrl;
    const pdfUrlCurrent = viewer.dataset.pdfUrlCurrent || '';
    // Спринт 8: карта URL всех доступных версий — цель сравнения
    // выбирается в меню, сравнение доступно из любой версии.
    const compareUrls = (function () {
        try {
            const raw = viewer.dataset.pdfUrls || '';
            return raw ? JSON.parse(raw) : {};
        } catch (error) {
            return {};
        }
    })();
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
    const STATUS_FALLBACK_COLOR = '#4674A4';

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
        page.layer.querySelectorAll('.pdf-markup, .pdf-pin, .pdf-pin-cluster, .pdf-cluster-popup')
            .forEach(function (mark) {
                mark.remove();
            });

        // Волна 3: кластеризация перекрывающихся маркеров. Пины, центры
        // которых ближе CLUSTER_THRESHOLD_PX на экране, сворачиваются в один
        // бейдж с количеством; клик — приблизить к группе или раскрыть список.
        const items = [];
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

                items.push({
                    issue: issue,
                    cx: x,
                    cy: y,
                    x1: x1,
                    y1: y1,
                    x2: x2,
                    y2: y2,
                });
            });

        const clusters = clusterMarkers(
            items,
            page.layer.clientWidth || 800,
            page.layer.clientHeight || 1000,
            CLUSTER_THRESHOLD_PX
        );

        clusters.forEach(function (cluster) {
            if (cluster.items.length === 1) {
                const item = cluster.items[0];
                drawMarkupFrame(page, item);
            } else {
                drawClusterBadge(page, cluster);
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

    const CLUSTER_THRESHOLD_PX = 28;

    function clusterMarkers(items, layerWidth, layerHeight, thresholdPx) {
        /* Жадная группировка по экранным пикселям: при увеличении масштаба
           кластер естественно распадается, потому что drawMarks вызывается
           после каждого рендера. */
        const clusters = [];
        items.forEach(function (item) {
            let best = null;
            let bestDistance = Infinity;
            clusters.forEach(function (cluster) {
                const dx = (cluster.cx - item.cx) * layerWidth;
                const dy = (cluster.cy - item.cy) * layerHeight;
                const distance = Math.sqrt(dx * dx + dy * dy);
                if (distance < thresholdPx && distance < bestDistance) {
                    best = cluster;
                    bestDistance = distance;
                }
            });
            if (best) {
                best.items.push(item);
                const n = best.items.length;
                best.cx = best.items.reduce(function (sum, part) {
                    return sum + part.cx;
                }, 0) / n;
                best.cy = best.items.reduce(function (sum, part) {
                    return sum + part.cy;
                }, 0) / n;
                best.x1 = Math.min(best.x1, item.x1);
                best.y1 = Math.min(best.y1, item.y1);
                best.x2 = Math.max(best.x2, item.x2);
                best.y2 = Math.max(best.y2, item.y2);
            } else {
                clusters.push({
                    cx: item.cx,
                    cy: item.cy,
                    x1: item.x1,
                    y1: item.y1,
                    x2: item.x2,
                    y2: item.y2,
                    items: [item],
                });
            }
        });
        return clusters;
    }

    function drawMarkupFrame(page, item) {
        const issue = item.issue;

        const frame = document.createElement('div');
        frame.className = 'pdf-markup';
        frame.dataset.issueId = issue.id;
        frame.style.left = (item.x1 * 100) + '%';
        frame.style.top = (item.y1 * 100) + '%';
        frame.style.width = ((item.x2 - item.x1) * 100) + '%';
        frame.style.height = ((item.y2 - item.y1) * 100) + '%';
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
    }

    function drawClusterBadge(page, cluster) {
        /* Доминирующий статус — цвет кластера. */
        const counts = {};
        cluster.items.forEach(function (item) {
            counts[item.issue.status] = (counts[item.issue.status] || 0) + 1;
        });
        const dominantStatus = Object.keys(counts).sort(function (a, b) {
            return counts[b] - counts[a];
        })[0];

        const badge = document.createElement('button');
        badge.type = 'button';
        badge.className = 'pdf-pin-cluster';
        badge.style.left = (cluster.cx * 100) + '%';
        badge.style.top = (cluster.cy * 100) + '%';
        badge.style.backgroundColor = statusColor(dominantStatus);
        badge.style.color = ['IN_PROGRESS', 'DISCUSSION'].includes(dominantStatus)
            ? '#212529'
            : '#fff';
        badge.textContent = String(cluster.items.length);
        badge.title = 'Замечаний в группе: ' + cluster.items.length
            + ' — клик приблизит, повторный клик раскроет список';
        badge.setAttribute('aria-label', 'Группа из ' + cluster.items.length + ' замечаний');

        badge.addEventListener('click', function (event) {
            event.stopPropagation();
            const existingPopup = page.layer.querySelector('.pdf-cluster-popup');
            if (existingPopup) {
                existingPopup.remove();
            }
            const spreadPx = clusterSpreadPx(page, cluster);
            if (spreadPx <= CLUSTER_THRESHOLD_PX * 1.6) {
                zoomToRectangle(cluster.x1, cluster.y1, cluster.x2, cluster.y2);
                return;
            }
            openClusterPopup(page, cluster);
        });

        page.layer.appendChild(badge);
    }

    function clusterSpreadPx(page, cluster) {
        const rect = page.layer.getBoundingClientRect();
        if (!rect.width || !rect.height) {
            return Infinity;
        }
        return Math.sqrt(
            Math.pow((cluster.x2 - cluster.x1) * rect.width, 2)
            + Math.pow((cluster.y2 - cluster.y1) * rect.height, 2)
        );
    }

    function openClusterPopup(page, cluster) {
        const popup = document.createElement('div');
        popup.className = 'pdf-cluster-popup';
        popup.style.left = (cluster.cx * 100) + '%';
        popup.style.top = (cluster.cy * 100) + '%';

        const head = document.createElement('div');
        head.className = 'pdf-cluster-popup-head';
        head.textContent = 'Замечания группы: ' + cluster.items.length;
        popup.appendChild(head);

        cluster.items.forEach(function (item) {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'pdf-cluster-popup-item';
            row.innerHTML = '<span class="pdf-cluster-popup-id" style="background:'
                + statusColor(item.issue.status) + '">#' + item.issue.id + '</span>'
                + '<span class="pdf-cluster-popup-text"></span>';
            row.querySelector('.pdf-cluster-popup-text').textContent =
                (item.issue.description || '').slice(0, 60);
            row.addEventListener('click', function (event) {
                event.stopPropagation();
                popup.remove();
                if (window.selectIssueFromPdf) {
                    window.selectIssueFromPdf(item.issue.id);
                }
            });
            popup.appendChild(row);
        });

        popup.addEventListener('click', function (event) {
            event.stopPropagation();
        });
        page.layer.appendChild(popup);

        setTimeout(function () {
            document.addEventListener('click', function handler() {
                if (popup.parentNode) {
                    popup.remove();
                }
                document.removeEventListener('click', handler);
            });
        }, 0);
    }

    function zoomToRectangle(x1, y1, x2, y2) {
        /* Приближает текущий лист так, чтобы прямоугольник (доли 0..1)
           занял ~70% окна вьювера, и прокручивает к его центру. */
        const entry = pages.get(currentPage);
        if (!entry || !viewer) {
            return;
        }
        const canvas = entry.canvas;
        const width = canvas.clientWidth || canvas.width;
        const height = canvas.clientHeight || canvas.height;
        const viewerRect = viewer.getBoundingClientRect();
        const boxWidth = Math.max(1, (x2 - x1) * width);
        const boxHeight = Math.max(1, (y2 - y1) * height);
        let nextScale = Math.min(
            (viewerRect.width * 0.7) / boxWidth,
            (viewerRect.height * 0.7) / boxHeight
        );
        nextScale = Math.min(4, Math.max(0.5, nextScale));

        const doScroll = function () {
            const fresh = pages.get(currentPage);
            if (!fresh) {
                return;
            }
            const rect = fresh.canvas.getBoundingClientRect();
            const box = viewer.getBoundingClientRect();
            const centerX = rect.left + ((x1 + x2) / 2) * rect.width;
            const centerY = rect.top + ((y1 + y2) / 2) * rect.height;
            viewer.scrollBy({
                left: centerX - box.left - box.width / 2,
                top: centerY - box.top - box.height / 2,
                behavior: 'smooth',
            });
        };

        if (Math.abs(nextScale - currentScale) > 0.01) {
            window.setZoom(nextScale);
            /* renderPages внутри setZoom асинхронный — ждём рендер и центрируем */
            setTimeout(doScroll, 350);
        } else {
            doScroll();
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
        updateThumbsActive();
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
            } else if (action === 'thumbs') {
                toggleThumbs();
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

        /* Спринт 3 (P1): Pointer Events вместо мыши — разделитель тянут
           пальцем и стилусом на планшете стройплощадки, не только мышью.
           setPointerCapture удерживает события за разделителем даже при
           выходе курсора за пределы кадра; touch-action: none в CSS уже
           запрещает прокрутку страницы во время перетаскивания. */
        divider.addEventListener('pointerdown', function (event) {
            if (!comparison.active || comparison.mode !== 'slider') {
                return;
            }
            if (event.pointerType === 'mouse' && event.button !== 0) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            dragging = true;
            try {
                divider.setPointerCapture(event.pointerId);
            } catch (error) {
                /* старые браузеры без capture — останемся на window-событиях */
            }
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

        function sliderSplitFromEvent(event) {
            const rect = frame.getBoundingClientRect();
            if (!rect.width) {
                return;
            }
            setSliderSplit(((event.clientX - rect.left) / rect.width) * 100);
        }

        function endSliderDrag() {
            if (!dragging) {
                return;
            }
            dragging = false;
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
        }

        /* Захваченные события приходят на сам разделитель; window-слушатели
           оставлены как фолбэк для браузеров без setPointerCapture. */
        divider.addEventListener('pointermove', function (event) {
            if (dragging && divider.hasPointerCapture
                    && divider.hasPointerCapture(event.pointerId)) {
                sliderSplitFromEvent(event);
            }
        });
        divider.addEventListener('pointerup', endSliderDrag);
        divider.addEventListener('pointercancel', endSliderDrag);

        window.addEventListener('mousemove', function (event) {
            if (!dragging) {
                return;
            }
            sliderSplitFromEvent(event);
        });

        window.addEventListener('mouseup', endSliderDrag);
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
                    viewerNotify(error.message);
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

    function compareTargetUrl() {
        const select = document.getElementById('compareVersionSelect');
        if (select && select.value && compareUrls[select.value]) {
            return compareUrls[select.value];
        }
        return pdfUrlCurrent || null;
    }

    function compareTargetLabel() {
        const select = document.getElementById('compareVersionSelect');
        if (select && select.selectedOptions && select.selectedOptions.length) {
            return select.selectedOptions[0].textContent.trim();
        }
        return 'текущая версия';
    }

    function hasCompareTarget() {
        return Boolean(compareTargetUrl() && pdfUrl);
    }

    function updateCompareMenu() {
        const button = document.getElementById('compareMenuBtn');
        if (!button) {
            return;
        }
        const label = document.getElementById('compareMenuLabel');
        const closeItem = document.getElementById('compareCloseItem');
        const closeDivider = document.getElementById('compareCloseDivider');
        const overlayControls = document.getElementById('overlayControls');
        if (label) {
            label.textContent = comparison.active
                ? 'Сравнение: '
                  + (comparison.mode === 'side' ? 'рядом'
                     : comparison.mode === 'slider' ? 'шторка' : 'наложение')
                : 'Сравнить';
        }
        // Спринт 8-фикс: disabled-кнопка без объяснения выглядит как
        // сломанный dropdown («не реагирует»). Объясняем, почему сравнение
        // недоступно, и возвращаем рабочую подсказку, когда цель есть.
        button.disabled = !hasCompareTarget();
        button.title = button.disabled
            ? (pdfUrl
                ? 'Нечего сравнивать: у документа только одна версия'
                : 'Сравнение недоступно: нет версий документа')
            : 'Сравнение версий: рядом, шторка, наложение';
        if (closeItem) {
            closeItem.classList.toggle('d-none', !comparison.active);
        }
        if (closeDivider) {
            closeDivider.classList.toggle('d-none', !comparison.active);
        }
        if (overlayControls) {
            overlayControls.classList.toggle(
                'd-none',
                !(comparison.active && comparison.mode === 'overlay'),
            );
        }
        button.classList.toggle('active', comparison.active);
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
                /* Спринт 8: в режиме «Наложение» верхний слой (открытая
                   версия) красится в синий, как в Vitro-CAD. pdf.js 3.11
                   принимает pageColors прямо в render(): линии/текст рисуются
                   foreground-цветом на background-фоне. Белый фон остаётся
                   белым, поэтому работает любой mix-blend-mode из меню;
                   «Рядом» и «Шторка» рендерятся как раньше. */
                pageColors: comparison.mode === 'overlay'
                    && pane.viewer.id === 'overlayViewerSelected'
                    ? OVERLAY_TOP_PAGE_COLORS
                    : null,
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
            if (comparison.mode === 'overlay') {
                updateOverlayFrame();
            }
        });
    }

    function startComparison(mode, selectedDocument, currentDocument) {
        const targetUrl = compareTargetUrl();
        if (!pdfUrl || !targetUrl) {
            return null;
        }
        const compareStage = document.getElementById('compareStage');
        const sliderStage = document.getElementById('sliderStage');
        const overlayStage = document.getElementById('overlayStage');
        const stage = document.getElementById('pdf-panel');
        const paneIds = {
            side: ['compareViewerSelected', 'compareViewerCurrent'],
            slider: ['sliderViewerSelected', 'sliderViewerCurrent'],
            overlay: ['overlayViewerSelected', 'overlayViewerCurrent'],
        };
        const ids = paneIds[mode];
        if (!ids) {
            return null;
        }
        const selectedViewer = document.getElementById(ids[0]);
        const currentViewer = document.getElementById(ids[1]);
        if (!compareStage || !sliderStage || !overlayStage
                || !selectedViewer || !currentViewer) {
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
        updateMinimapVisibility();
        updateThumbsVisibility();
        comparison.selected = comparisonPane(selectedViewer, pdfUrl, false);
        comparison.current = comparisonPane(currentViewer, targetUrl, true);
        comparison.selected.pdfDocument = selectedDocument || null;
        comparison.current.pdfDocument = currentDocument || null;
        bindComparisonPane(comparison.selected);
        bindComparisonPane(comparison.current);

        if (mode === 'slider') {
            setSliderSplit(50);
        }
        if (mode === 'overlay') {
            resetOverlayOffset();
        }

        compareStage.classList.toggle('d-none', mode !== 'side');
        sliderStage.classList.toggle('d-none', mode !== 'slider');
        overlayStage.classList.toggle('d-none', mode !== 'overlay');
        viewer.classList.add('d-none');
        setFitButtonsEnabled(false);
        if (stage) {
            stage.classList.toggle('is-comparing', mode === 'side');
            stage.classList.toggle('is-sliding', mode === 'slider');
            stage.classList.toggle('is-overlay', mode === 'overlay');
        }
        // Подписи панелей «рядом»: слева — открытая версия, справа — цель.
        compareStage.querySelectorAll('.compare-pane-head').forEach(
            function (head, index) {
                head.textContent = index === 0
                    ? 'Выбранная версия '
                      + (viewer.dataset.selectedVersionNumber
                         ? 'v' + viewer.dataset.selectedVersionNumber : '')
                    : 'Цель сравнения — ' + compareTargetLabel();
            },
        );
        updateCompareMenu();
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
        if (!hasCompareTarget()) {
            return;
        }
        if (comparison.active) {
            closeComparison();
        }
        comparison.requestId += 1;
        comparison.loading = false;
        updateCompareMenu();
        startComparison('side');
    }

    function prepareAlignedComparison(mode) {
        // Шторка и наложение требуют одинаковой геометрии листов.
        if (comparison.active && comparison.mode === mode) {
            closeComparison();
            return;
        }
        if (!hasCompareTarget() || comparison.loading) {
            return;
        }
        const targetUrl = compareTargetUrl();
        const requestId = comparison.requestId + 1;
        comparison.requestId = requestId;
        comparison.loading = true;
        updateCompareMenu();

        Promise.all([
            window.pdfjsLib.getDocument(pdfUrl).promise,
            window.pdfjsLib.getDocument(targetUrl).promise,
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
                    updateCompareMenu();
                    viewerNotify(
                        mode === 'overlay'
                            ? 'Наложение доступно только для листов с одинаковой геометрией. Используйте режим «Рядом».'
                            : 'Шторка доступна только для листов с одинаковой геометрией. Используйте режим «Рядом».'
                    );
                    return null;
                }
                comparison.geometryMismatch = false;
                comparison.loading = false;
                if (comparison.active) {
                    closeComparison();
                }
                startComparison(mode, documents[0], documents[1]);
                return null;
            });
        }).catch(function () {
            if (requestId !== comparison.requestId) {
                return;
            }
            comparison.loading = false;
            updateCompareMenu();
            viewerNotify('Не удалось загрузить PDF для сравнения.');
        });
    }

    function closeComparison() {
        if (!comparison.active) {
            return;
        }
        const page = comparison.page;
        comparison.active = false;
        comparison.mode = null;
        updateMinimapVisibility();
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
        const overlayStage = document.getElementById('overlayStage');
        const stage = document.getElementById('pdf-panel');
        if (compareStage) {
            compareStage.classList.add('d-none');
        }
        if (sliderStage) {
            sliderStage.classList.add('d-none');
        }
        if (overlayStage) {
            overlayStage.classList.add('d-none');
        }
        if (stage) {
            stage.classList.remove('is-comparing');
            stage.classList.remove('is-sliding');
            stage.classList.remove('is-overlay');
        }
        viewer.classList.remove('d-none');
        setFitButtonsEnabled(true);
        resetOverlayOffset();
        updateMinimapVisibility();
        updateThumbsVisibility();
        updateCompareMenu();

        if (pdfDocument) {
            currentPage = page;
            renderPages();
        } else {
            updatePageControls();
        }
    }

    /* ---------- Спринт 8: наложение чертежей ---------- */

    /* Спринт 8: цвет линий верхнего слоя наложения — синий «как в Витро».
       Фон оставляем белым: при mix-blend-mode: multiply белый верхнего
       слоя становится прозрачным, видны только синие линии. */
    const OVERLAY_TOP_PAGE_COLORS = {
        background: '#FFFFFF',
        foreground: '#2563EB',
    };

    const overlayState = { opacity: 1, blend: 'multiply', offsetX: 0, offsetY: 0 };

    function overlayTopPane() {
        return document.getElementById('overlayViewerSelected');
    }

    function overlayFrameEl() {
        return document.getElementById('overlayFrame');
    }

    function applyOverlayStyle() {
        const top = overlayTopPane();
        if (top) {
            top.style.opacity = String(overlayState.opacity);
            top.style.mixBlendMode = overlayState.blend;
            top.style.transform = 'translate('
                + overlayState.offsetX + 'px, ' + overlayState.offsetY + 'px)';
        }
        const label = document.getElementById('overlayOpacityValue');
        if (label) {
            label.textContent = Math.round(overlayState.opacity * 100) + '%';
        }
    }

    function updateOverlayFrame() {
        const pane = comparison.current || comparison.selected;
        const frame = overlayFrameEl();
        if (!pane || !pane.viewport || !frame) {
            return;
        }
        frame.style.width = Math.ceil(pane.viewport.width) + 'px';
        frame.style.height = Math.ceil(pane.viewport.height) + 'px';
        applyOverlayStyle();
    }

    function resetOverlayOffset() {
        overlayState.offsetX = 0;
        overlayState.offsetY = 0;
        applyOverlayStyle();
    }

    function initOverlay() {
        const opacity = document.getElementById('overlayOpacity');
        if (opacity) {
            opacity.addEventListener('input', function () {
                overlayState.opacity = Number(opacity.value) / 100;
                applyOverlayStyle();
            });
        }
        const blend = document.getElementById('overlayBlend');
        if (blend) {
            blend.addEventListener('change', function () {
                overlayState.blend = blend.value;
                applyOverlayStyle();
            });
        }
        document.addEventListener('click', function (event) {
            const nudge = event.target.closest('[data-overlay-nudge]');
            if (nudge) {
                event.preventDefault();
                const parts = (nudge.getAttribute('data-overlay-nudge') || '').split(',');
                const direction = parts[0];
                const dx = direction === 'left' ? -1 : direction === 'right' ? 1 : 0;
                const dy = direction === 'up' ? -1 : direction === 'down' ? 1 : 0;
                const step = event.shiftKey ? 10 : 1;
                overlayState.offsetX += dx * step;
                overlayState.offsetY += dy * step;
                applyOverlayStyle();
                return;
            }
            if (event.target.closest('#overlayResetOffset')) {
                event.preventDefault();
                resetOverlayOffset();
            }
        });

        const scroll = document.getElementById('overlayScroll');
        if (!scroll) {
            return;
        }
        let dragging = false;
        let startX = 0;
        let startY = 0;
        let startOffsetX = 0;
        let startOffsetY = 0;
        scroll.addEventListener('pointerdown', function (event) {
            if (!comparison.active || comparison.mode !== 'overlay') {
                return;
            }
            if (event.button !== 0 || event.target.closest('.overlay-hud')) {
                return;
            }
            dragging = true;
            startX = event.clientX;
            startY = event.clientY;
            startOffsetX = overlayState.offsetX;
            startOffsetY = overlayState.offsetY;
            scroll.classList.add('is-dragging');
            event.preventDefault();
        });
        window.addEventListener('pointermove', function (event) {
            if (!dragging) {
                return;
            }
            overlayState.offsetX = startOffsetX + (event.clientX - startX);
            overlayState.offsetY = startOffsetY + (event.clientY - startY);
            applyOverlayStyle();
        });
        window.addEventListener('pointerup', function () {
            dragging = false;
            scroll.classList.remove('is-dragging');
        });
        scroll.addEventListener('wheel', function (event) {
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

    /* ---------- Спринт 8: полоса листов (миниатюры) ---------- */

    const thumbs = { open: false, built: false, items: new Map(), observer: null };

    function thumbRail() {
        return document.getElementById('thumbRail');
    }

    function updateThumbsVisibility() {
        const rail = thumbRail();
        const toggle = document.getElementById('thumbsToggle');
        if (!rail) {
            return;
        }
        const shown = thumbs.open && !comparison.active;
        rail.classList.toggle('d-none', !shown);
        if (toggle) {
            toggle.classList.toggle('active', shown);
            toggle.setAttribute('aria-expanded', shown ? 'true' : 'false');
        }
        if (shown && pdfDocument) {
            buildThumbRail();
        }
    }

    function toggleThumbs() {
        thumbs.open = !thumbs.open;
        updateThumbsVisibility();
    }
    window.toggleThumbs = toggleThumbs;

    function buildThumbRail() {
        const rail = thumbRail();
        if (!rail || !pdfDocument || thumbs.built) {
            return;
        }
        thumbs.built = true;
        rail.innerHTML = '';
        thumbs.items.clear();
        for (let number = 1; number <= pdfDocument.numPages; number += 1) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'thumb';
            button.dataset.page = String(number);
            button.setAttribute('aria-label', 'Страница ' + number);
            const canvas = document.createElement('canvas');
            canvas.width = 160;
            canvas.height = 226;
            canvas.dataset.rendered = '0';
            const label = document.createElement('span');
            label.className = 'thumb__page num';
            label.textContent = String(number);
            button.appendChild(canvas);
            button.appendChild(label);
            button.addEventListener('click', function () {
                window.goToPage(number);
            });
            rail.appendChild(button);
            thumbs.items.set(number, { button: button, canvas: canvas });
        }
        if ('IntersectionObserver' in window) {
            thumbs.observer = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        renderThumb(Number(entry.target.dataset.page));
                    }
                });
            }, { root: rail, rootMargin: '240px' });
            thumbs.items.forEach(function (item) {
                thumbs.observer.observe(item.button);
            });
        } else {
            thumbs.items.forEach(function (item) {
                renderThumb(Number(item.button.dataset.page));
            });
        }
        updateThumbsActive();
    }

    function renderThumb(pageNumber) {
        const item = thumbs.items.get(pageNumber);
        if (!item || item.canvas.dataset.rendered === '1' || !pdfDocument) {
            return;
        }
        item.canvas.dataset.rendered = '1';
        pdfDocument.getPage(pageNumber).then(function (page) {
            const base = page.getViewport({ scale: 1, rotation: currentRotation });
            const scale = 140 / base.width;
            const viewport = page.getViewport({ scale: scale, rotation: currentRotation });
            const outputScale = window.devicePixelRatio || 1;
            item.canvas.width = Math.floor(viewport.width * outputScale);
            item.canvas.height = Math.floor(viewport.height * outputScale);
            item.canvas.style.width = Math.floor(viewport.width) + 'px';
            item.canvas.style.height = Math.floor(viewport.height) + 'px';
            const context = item.canvas.getContext('2d');
            context.setTransform(outputScale, 0, 0, outputScale, 0, 0);
            return page.render({
                canvasContext: context,
                viewport: viewport,
            }).promise;
        }).catch(function () {
            item.canvas.dataset.rendered = '0';
        });
    }

    function scrollThumbIntoRail(button) {
        const rail = thumbRail();
        if (!rail || !button) {
            return;
        }
        // Прокручиваем только саму полосу листов: scrollIntoView()
        // цеплял ещё и .stage и уводил основной вьювер вниз.
        const buttonRect = button.getBoundingClientRect();
        const railRect = rail.getBoundingClientRect();
        if (buttonRect.top < railRect.top) {
            rail.scrollTop += buttonRect.top - railRect.top - 6;
        } else if (buttonRect.bottom > railRect.bottom) {
            rail.scrollTop += buttonRect.bottom - railRect.bottom + 6;
        }
    }

    function updateThumbsActive() {
        if (!thumbs.items.size) {
            return;
        }
        const activePage = comparison.active ? comparison.page : currentPage;
        thumbs.items.forEach(function (item, number) {
            item.button.classList.toggle('is-active', number === activePage);
        });
        const active = thumbs.items.get(activePage);
        if (active && thumbs.open) {
            scrollThumbIntoRail(active.button);
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
                    viewerNotify(error.message);
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

    function updateMinimapVisibility() {
        if (!minimapBox) {
            return;
        }
        minimapBox.style.display = comparison.active ? 'none' : '';
    }

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

    function focusViewerPage(pageNumber) {
        const entry = pages.get(Number(pageNumber));
        if (!entry) {
            return;
        }
        const stage = viewer.closest('.stage') || viewer.parentElement;
        if (!stage) {
            return;
        }
        const pageRect = entry.wrapper.getBoundingClientRect();
        const stageRect = stage.getBoundingClientRect();
        const target = stage.scrollTop + pageRect.top - stageRect.top - 12;
        stage.scrollTo({
            top: Math.max(0, target),
            left: 0,
            behavior: 'smooth',
        });
    }

    window.goToPage = function (pageNumber) {
        if (comparison.active) {
            return renderComparisonPage(pageNumber);
        }
        const rendered = renderPage(pageNumber);
        if (rendered && typeof rendered.then === 'function') {
            return rendered.then(function () {
                focusViewerPage(pageNumber);
            });
        }
        return rendered;
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

    /* Спринт 3 (P3): горячие клавиши N (следующая) и P (предыдущая).
       Ловим физические клавиши (event.code), чтобы работало в любой
       раскладке — ЙЦУКЕН в том числе; в полях ввода не срабатываем. */
    function shortcutGuard() {
        const active = document.activeElement;
        if (!active) {
            return false;
        }
        const tag = (active.tagName || '').toLowerCase();
        return tag === 'input' || tag === 'textarea' || tag === 'select'
            || active.isContentEditable;
    }
    document.addEventListener('keydown', function (event) {
        if (event.defaultPrevented || event.ctrlKey || event.metaKey
                || event.altKey || shortcutGuard()) {
            return;
        }
        if (event.code === 'KeyN') {
            event.preventDefault();
            window.nextPage();
        } else if (event.code === 'KeyP') {
            event.preventDefault();
            window.prevPage();
        }
    });

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

    window.renderPage = renderPage;
    window.getCurrentPage = function () {
        return comparison.active ? comparison.page : currentPage;
    };
    window.closePdfComparison = closeComparison;

    /* Спринт 8: меню сравнения — режимы и цель выбираются в одном месте */
    const compareSelect = document.getElementById('compareVersionSelect');
    if (compareSelect) {
        compareSelect.addEventListener('change', function () {
            comparison.geometryMismatch = false;
            if (comparison.active) {
                const mode = comparison.mode;
                closeComparison();
                if (mode === 'side') {
                    startComparison('side');
                } else {
                    prepareAlignedComparison(mode);
                }
            }
        });
    }
    /* Меню «Сравнить» лежит в тулбаре вьювера, а тот прокручивается по
       горизонтали (A21: .toolbar.viewer-toolbar — overflow-x: auto,
       overflow-y: hidden). Bootstrap честно открывает dropdown по клику,
       но раскрытое меню обрезается этим overflow и становится невидимым —
       кажется, что кнопка не реагирует. Явно создаём инстанс Dropdown со
       стратегией Popper «fixed»: меню позиционируется от вьюпорта и
       выходит из-под обрезки тулбара. Обработчик data-bs-toggle на кнопке
       не трогаем — getOrCreateInstance в data-api вернёт наш инстанс. */
    const compareMenuBtn = document.getElementById('compareMenuBtn');
    if (compareMenuBtn && window.bootstrap && window.bootstrap.Dropdown) {
        window.bootstrap.Dropdown.getOrCreateInstance(compareMenuBtn, {
            popperConfig: {strategy: 'fixed'},
        });
    }
    document.addEventListener('click', function (event) {
        const item = event.target.closest('[data-compare-mode]');
        if (item) {
            event.preventDefault();
            const mode = item.getAttribute('data-compare-mode');
            if (mode === 'side') {
                openComparison();
            } else {
                prepareAlignedComparison(mode);
            }
            return;
        }
        if (event.target.closest('#compareCloseItem')) {
            event.preventDefault();
            closeComparison();
        }
    });
    updateCompareMenu();

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
    initOverlay();
    initContextMenu();
    initDetails();
    initMinimap();
    updateThumbsVisibility();
    if (issues.length) {
        selectIssue(issues[0].id);
    }
})();
