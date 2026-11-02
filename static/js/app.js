(function () {
    'use strict';

    var SIDEBAR_WIDTH_KEY = 'sod:sidebar:width';
    var SIDEBAR_COLLAPSED_KEY = 'sod:sidebar:collapsed';
    var SIDEBAR_MIN_WIDTH = 200;
    var SIDEBAR_MAX_WIDTH = 500;
    var COLUMN_ORDER_PREFIX = 'sod:columns:';

    function readStorage(key, fallback) {
        try {
            return window.localStorage.getItem(key) || fallback;
        } catch (error) {
            return fallback;
        }
    }

    function writeStorage(key, value) {
        try {
            window.localStorage.setItem(key, value);
        } catch (error) {
            /* localStorage может быть недоступен */
        }
    }

    function panel() {
        return document.getElementById('documentsPanel');
    }

    function documentsTable() {
        return document.getElementById('documentsTable');
    }

    /* ---------- Sidebar ---------- */

    function initSidebar() {
        var shell = document.getElementById('appShell');
        if (!shell) {
            return;
        }

        if (readStorage(SIDEBAR_COLLAPSED_KEY, '0') === '1') {
            shell.classList.add('sidebar-collapsed');
        }

        var collapse = document.getElementById('sidebarCollapse');
        if (collapse) {
            collapse.addEventListener('click', function () {
                shell.classList.add('sidebar-collapsed');
                writeStorage(SIDEBAR_COLLAPSED_KEY, '1');
            });
        }

        var restore = document.getElementById('sidebarRestore');
        if (restore) {
            restore.addEventListener('click', function () {
                shell.classList.remove('sidebar-collapsed');
                writeStorage(SIDEBAR_COLLAPSED_KEY, '0');
            });
        }

        var resizer = document.getElementById('sidebarResizer');
        if (!resizer) {
            return;
        }

        var storedWidth = parseInt(readStorage(SIDEBAR_WIDTH_KEY, ''), 10);

        function setWidth(width) {
            var next = Math.max(
                SIDEBAR_MIN_WIDTH,
                Math.min(SIDEBAR_MAX_WIDTH, Math.round(width))
            );
            shell.style.setProperty('--app-sidebar-w', next + 'px');
            return next;
        }

        if (!isNaN(storedWidth)) {
            setWidth(storedWidth);
        }

        resizer.addEventListener('mousedown', function (event) {
            event.preventDefault();
            var startX = event.clientX;
            var startWidth = parseInt(
                getComputedStyle(shell).getPropertyValue('--app-sidebar-w'),
                10
            ) || 250;
            resizer.classList.add('is-active');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';

            function onMove(moveEvent) {
                setWidth(startWidth + (moveEvent.clientX - startX));
            }

            function onUp() {
                resizer.classList.remove('is-active');
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                var width = parseInt(
                    getComputedStyle(shell).getPropertyValue('--app-sidebar-w'),
                    10
                );
                if (!isNaN(width)) {
                    writeStorage(SIDEBAR_WIDTH_KEY, String(width));
                }
            }

            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
    }

    /* ---------- Bulk selection ---------- */

    function updateBulkBar() {
        var form = document.getElementById('bulkForm');
        var bar = document.getElementById('bulkBar');
        var count = document.getElementById('bulkCount');
        if (!form || !bar || !count) {
            return;
        }
        var selected = form.querySelectorAll('.doc-select:checked').length;
        count.textContent = selected;
        bar.classList.toggle('d-none', selected === 0);

        var selectAll = document.getElementById('selectAllDocuments');
        var all = form.querySelectorAll('.doc-select');
        if (selectAll) {
            selectAll.checked = all.length > 0 && selected === all.length;
            selectAll.indeterminate = selected > 0 && selected < all.length;
        }
    }

    function initBulkSelection() {
        document.addEventListener('change', function (event) {
            if (event.target.id === 'selectAllDocuments') {
                var form = document.getElementById('bulkForm');
                if (!form) {
                    return;
                }
                form.querySelectorAll('.doc-select').forEach(function (box) {
                    box.checked = event.target.checked;
                });
                updateBulkBar();
                return;
            }
            if (event.target.classList && event.target.classList.contains('doc-select')) {
                updateBulkBar();
            }
        });
    }

    /* ---------- Column order (SortableJS) ---------- */

    function columnOrderKey() {
        var root = panel();
        if (!root) {
            return null;
        }
        return COLUMN_ORDER_PREFIX + (root.dataset.projectId || '0') + ':' +
            (root.dataset.viewId || 'none');
    }

    function columnKeys() {
        var table = documentsTable();
        if (!table) {
            return [];
        }
        return Array.prototype.slice.call(
            table.querySelectorAll('thead th[data-column]')
        ).map(function (th) {
            return th.dataset.column;
        });
    }

    function reorderCells(keys) {
        var table = documentsTable();
        if (!table) {
            return;
        }
        var rows = table.querySelectorAll('thead tr, tbody tr');
        rows.forEach(function (row) {
            var cells = {};
            row.querySelectorAll('[data-column]').forEach(function (cell) {
                cells[cell.dataset.column] = cell;
            });
            keys.forEach(function (key) {
                if (cells[key]) {
                    row.appendChild(cells[key]);
                }
            });
        });
    }

    function applyStoredColumnOrder() {
        var key = columnOrderKey();
        var table = documentsTable();
        if (!key || !table) {
            return;
        }
        var stored;
        try {
            stored = JSON.parse(readStorage(key, '[]'));
        } catch (error) {
            stored = [];
        }
        if (!Array.isArray(stored) || !stored.length) {
            return;
        }
        var current = columnKeys();
        var ordered = ['selection'];
        stored.forEach(function (item) {
            if (item !== 'selection' && current.indexOf(item) !== -1 && ordered.indexOf(item) === -1) {
                ordered.push(item);
            }
        });
        current.forEach(function (item) {
            if (ordered.indexOf(item) === -1) {
                ordered.push(item);
            }
        });
        reorderCells(ordered);
    }

    function persistColumnOrder() {
        var key = columnOrderKey();
        if (!key) {
            return;
        }
        var order = columnKeys().filter(function (item) {
            return item !== 'selection';
        });
        writeStorage(key, JSON.stringify(order));
    }

    function initColumnOrder() {
        var table = documentsTable();
        if (!table || typeof window.Sortable === 'undefined') {
            return;
        }
        applyStoredColumnOrder();

        var headerRow = table.querySelector('thead tr');
        if (!headerRow || headerRow.dataset.sortableReady === '1') {
            return;
        }
        headerRow.dataset.sortableReady = '1';
        headerRow.querySelectorAll('th a').forEach(function (link) {
            link.setAttribute('draggable', 'false');
        });

        window.Sortable.create(headerRow, {
            animation: 150,
            draggable: 'th',
            filter: '.doc-select-col',
            onEnd: function () {
                var selection = headerRow.querySelector('th.doc-select-col');
                if (selection && headerRow.firstElementChild !== selection) {
                    headerRow.insertBefore(selection, headerRow.firstElementChild);
                }
                reorderCells(columnKeys());
                persistColumnOrder();
            }
        });
    }

    /* ---------- Upload ---------- */

    function csrfToken() {
        var input = document.querySelector('[name=csrfmiddlewaretoken]');
        if (input) {
            return input.value;
        }
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function currentUploadTarget() {
        var params = new URLSearchParams(window.location.search);
        var folderId = params.get('folder');
        if (folderId) {
            var node = document.querySelector(
                '.folder-node[data-folder-id="' + folderId + '"]'
            );
            if (node) {
                return {
                    sectionId: node.dataset.sectionId || '',
                    folderId: folderId,
                    name: node.dataset.folderName || 'выбранную папку'
                };
            }
        }
        var sectionId = params.get('section');
        var section = null;
        if (sectionId) {
            section = document.querySelector(
                '.section-item[data-id="' + sectionId + '"]'
            );
        }
        if (!section) {
            section = document.querySelector('.section-item[data-id]');
        }
        if (!section) {
            return null;
        }
        return {
            sectionId: section.dataset.id,
            folderId: '',
            name: 'раздел ' + (section.dataset.code || '')
        };
    }

    function updateUploadTarget() {
        var target = currentUploadTarget();
        var label = document.getElementById('uploadTarget');
        var dropLabel = document.getElementById('dropTargetName');
        if (!target) {
            if (label) {
                label.textContent = 'Сначала создайте раздел';
            }
            return;
        }
        if (label) {
            label.textContent = 'Загрузка в ' + target.name;
        }
        if (dropLabel) {
            dropLabel.textContent = target.name;
        }
    }

    function setUploadStatus(text, isError) {
        var status = document.getElementById('uploadStatus');
        if (!status) {
            if (isError && text) {
                showDndToast(text, true);
            }
            return;
        }
        status.textContent = text || '';
        status.classList.toggle('d-none', !text);
        status.classList.toggle('is-error', !!isError);
    }

    function showDropOverlay(visible) {
        var overlay = document.getElementById('docDropOverlay');
        var wrap = document.getElementById('documentsTableWrap');
        if (overlay) {
            overlay.hidden = !visible;
        }
        if (wrap) {
            wrap.classList.toggle('is-dragover', visible);
        }
        if (visible) {
            updateUploadTarget();
        }
    }

    function isFileDrag(event) {
        if (!event.dataTransfer || !event.dataTransfer.types) {
            return false;
        }
        return Array.prototype.indexOf.call(event.dataTransfer.types, 'Files') !== -1;
    }

    var uploadInProgress = false;
    var pendingUploadTarget = null;

    async function handleFiles(files) {
        if (uploadInProgress) {
            return;
        }
        var root = panel();
        var target = pendingUploadTarget || currentUploadTarget();
        pendingUploadTarget = null;
        if (!root || !target) {
            setUploadStatus('Сначала выберите раздел', true);
            return;
        }
        var uploadUrl = root.dataset.uploadUrl;
        if (!uploadUrl) {
            return;
        }

        uploadInProgress = true;
        setUploadStatus('Загрузка ' + files.length + ' файл(ов)…', false);
        var errors = [];
        var lastDocumentId = null;

        for (var index = 0; index < files.length; index += 1) {
            var file = files[index];
            var formData = new FormData();
            formData.append('section_id', target.sectionId);
            if (target.folderId) {
                formData.append('folder_id', target.folderId);
            }
            formData.append('file', file);
            formData.append('code', file.name.replace(/\.[^.]+$/, ''));
            formData.append('name', file.name);

            try {
                var response = await fetch(uploadUrl, {
                    method: 'POST',
                    headers: {'X-CSRFToken': csrfToken()},
                    body: formData
                });
                var data = await response.json();
                if (!response.ok) {
                    errors.push((data.error || 'Ошибка загрузки') + ': ' + file.name);
                } else {
                    lastDocumentId = response.headers.get('X-Document-Id') || data.document_id;
                }
            } catch (error) {
                errors.push(error.message + ': ' + file.name);
            }
        }

        if (errors.length) {
            setUploadStatus(errors.join('; '), true);
        }
        if (lastDocumentId) {
            var nextUrl = new URL(window.location.href);
            nextUrl.searchParams.delete('highlight');
            nextUrl.searchParams.set('highlight', lastDocumentId);
            window.location.href = nextUrl.toString();
            return;
        }

        uploadInProgress = false;
        var fileInput = document.getElementById('fileInput');
        if (fileInput) {
            fileInput.value = '';
        }
        if (!errors.length) {
            setUploadStatus('', false);
        }
    }

    function initUpload() {
        document.addEventListener('change', function (event) {
            if (event.target.id !== 'fileInput') {
                return;
            }
            if (event.target.files.length) {
                handleFiles(event.target.files);
            } else {
                pendingUploadTarget = null;
            }
        });

        document.addEventListener('dragenter', function (event) {
            if (!isFileDrag(event) || !event.target.closest('#documentsTableWrap')) {
                return;
            }
            event.preventDefault();
            showDropOverlay(true);
        });

        document.addEventListener('dragover', function (event) {
            if (!isFileDrag(event) || !event.target.closest('#documentsTableWrap')) {
                return;
            }
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
            showDropOverlay(true);
        });

        document.addEventListener('dragleave', function (event) {
            if (!isFileDrag(event) || !event.target.closest('#documentsTableWrap')) {
                return;
            }
            if (!document.getElementById('documentsTableWrap').contains(event.relatedTarget)) {
                showDropOverlay(false);
            }
        });

        document.addEventListener('drop', function (event) {
            if (!isFileDrag(event) || !event.target.closest('#documentsTableWrap')) {
                return;
            }
            event.preventDefault();
            showDropOverlay(false);
            if (event.dataTransfer.files.length) {
                handleFiles(event.dataTransfer.files);
            }
        });
    }

    /* ---------- Drag documents/folders into folders/sections ---------- */

    var dragDocumentId = null;
    var dragFolderId = null;
    var toastTimer = null;

    function dndTypes(event) {
        if (!event.dataTransfer || !event.dataTransfer.types) {
            return [];
        }
        return Array.prototype.slice.call(event.dataTransfer.types);
    }

    function isFolderDrag(event) {
        return dndTypes(event).indexOf('application/x-sod-folder-id') !== -1;
    }

    function isDocumentDrag(event) {
        var types = dndTypes(event);
        return types.indexOf('text/plain') !== -1 &&
            types.indexOf('Files') === -1 &&
            types.indexOf('application/x-sod-folder-id') === -1;
    }

    function isTreeDrag(event) {
        return isDocumentDrag(event) || isFolderDrag(event);
    }

    function numericEntityId(value) {
        var text = String(value || '').trim();
        if (/^\d+$/.test(text)) {
            return text;
        }
        var match = text.match(/(?:^|\/)(\d+)\/?(?:[?#].*)?$/);
        return match ? match[1] : '';
    }

    function dropTargets() {
        return document.querySelectorAll('[data-drop-folder], [data-drop-section]');
    }

    function clearDropHighlights() {
        dropTargets().forEach(function (target) {
            target.classList.remove('is-dnd-over');
        });
        document.querySelectorAll('#documentsTable tbody tr.is-dragging, .folder-row.is-dragging-folder').forEach(function (row) {
            row.classList.remove('is-dragging', 'is-dragging-folder');
        });
        dragDocumentId = null;
        dragFolderId = null;
    }

    function isArchiveZone(target) {
        var folderRow = target.closest('[data-folder-row]');
        if (folderRow && folderRow.dataset.zone === 'ARCHIVE') {
            return true;
        }
        return false;
    }

    function showDndToast(text, isError) {
        var toast = document.getElementById('dndToast');
        if (!toast) {
            return;
        }
        toast.textContent = text;
        toast.classList.remove('d-none');
        toast.classList.toggle('is-error', !!isError);
        window.clearTimeout(toastTimer);
        toastTimer = window.setTimeout(function () {
            toast.classList.add('d-none');
        }, 4500);
    }

    function refreshDocumentsPanel() {
        var current = document.getElementById('documentsPanel');
        if (!current) {
            window.location.reload();
            return;
        }
        fetch(window.location.href, {
            credentials: 'same-origin',
            headers: {'Accept': 'text/html'}
        }).then(function (response) {
            return response.text();
        }).then(function (html) {
            var parsed = new DOMParser().parseFromString(html, 'text/html');
            var freshPanel = parsed.getElementById('documentsPanel');
            if (!freshPanel) {
                window.location.reload();
                return;
            }
            current.replaceWith(freshPanel);
            initDocumentsPanel();
            refreshTreeCounts(parsed);
        }).catch(function () {
            window.location.reload();
        });
    }

    function refreshTreeCounts(sourceDocument) {
        sourceDocument.querySelectorAll('.folder-node[data-folder-id]').forEach(function (node) {
            var folderId = node.getAttribute('data-folder-id');
            var sourceCount = node.querySelector('.folder-count');
            var currentCount = document.querySelector(
                '.folder-node[data-folder-id="' + folderId + '"] .folder-count'
            );
            if (sourceCount && currentCount) {
                currentCount.textContent = sourceCount.textContent;
            }
        });
        sourceDocument.querySelectorAll('.section-count[data-section-id]').forEach(function (node) {
            var sectionId = node.getAttribute('data-section-id');
            var currentCount = document.querySelector(
                '.section-count[data-section-id="' + sectionId + '"]'
            );
            if (currentCount) {
                currentCount.textContent = node.textContent;
            }
        });
    }

    function apiUrl(template, entityId) {
        return String(template || '').replace(
            '/0/',
            '/' + encodeURIComponent(entityId) + '/'
        );
    }

    function postMoveRequest(url, body, successMessage, options) {
        options = options || {};
        fetch(url, {
            method: 'POST',
            headers: {'X-CSRFToken': csrfToken()},
            body: body
        }).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok) {
                    throw new Error(data.error || 'Не удалось выполнить операцию');
                }
                return data;
            });
        }).then(function (data) {
            showDndToast(successMessage(data), false);
            if (options.reloadTree) {
                window.location.reload();
            } else {
                refreshDocumentsPanel();
            }
        }).catch(function (error) {
            showDndToast(error.message, true);
        });
    }

    function initDocumentDragAndDrop() {
        document.querySelectorAll('[data-folder-row] a, .folder-row a').forEach(function (link) {
            link.setAttribute('draggable', 'false');
        });

        document.addEventListener('dragstart', function (event) {
            var row = event.target.closest('#documentsTable tbody tr[data-document-id]');
            if (row) {
                var documentId = numericEntityId(row.dataset.documentId);
                if (!documentId) {
                    return;
                }
                var zone = row.dataset.zone || '';
                if (zone && zone !== 'WIP') {
                    var zoneLabels = {
                        SHARED: 'на согласовании',
                        PUBLISHED: 'опубликован',
                        ARCHIVE: 'в архиве'
                    };
                    window.alert(
                        'Документ ' + (zoneLabels[zone] || 'вне зоны WIP') +
                        ', перемещение запрещено'
                    );
                    event.preventDefault();
                    return;
                }
                event.stopPropagation();
                dragDocumentId = documentId;
                dragFolderId = null;
                event.dataTransfer.effectAllowed = 'copyMove';
                event.dataTransfer.clearData();
                event.dataTransfer.setData('text/plain', documentId);
                event.dataTransfer.setData('application/x-sod-document-id', documentId);
                row.classList.add('is-dragging');
                showDropOverlay(false);
                return;
            }

            var folderRow = event.target.closest('[data-folder-row], .folder-row');
            if (!folderRow || event.target.closest('button, .dropdown-menu, input')) {
                return;
            }
            var folderNode = folderRow.closest('[data-folder-id]');
            var folderId = numericEntityId(
                folderRow.dataset.dropFolder ||
                folderRow.dataset.folderId ||
                (folderNode && folderNode.dataset.folderId)
            );
            if (!folderId) {
                return;
            }
            event.stopPropagation();
            dragDocumentId = null;
            dragFolderId = folderId;
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.clearData();
            event.dataTransfer.setData('application/x-sod-folder-id', folderId);
            event.dataTransfer.setData('text/plain', 'folder:' + folderId);
            folderRow.classList.add('is-dragging-folder');
            showDropOverlay(false);
        });

        document.addEventListener('dragend', function () {
            clearDropHighlights();
        });

        document.addEventListener('dragenter', function (event) {
            var target = event.target.closest('[data-drop-folder], [data-drop-section]');
            if (!target || !isTreeDrag(event)) {
                return;
            }
            event.preventDefault();
            target.classList.add('is-dnd-over');
        });

        document.addEventListener('dragover', function (event) {
            var target = event.target.closest('[data-drop-folder], [data-drop-section]');
            if (!target || !isTreeDrag(event)) {
                return;
            }
            event.preventDefault();
            event.dataTransfer.dropEffect = (
                isDocumentDrag(event) && (event.ctrlKey || event.metaKey)
            ) ? 'copy' : 'move';
            dropTargets().forEach(function (other) {
                other.classList.toggle('is-dnd-over', other === target);
            });
        });

        document.addEventListener('dragleave', function (event) {
            var target = event.target.closest('[data-drop-folder], [data-drop-section]');
            if (!target) {
                return;
            }
            if (!event.relatedTarget || !target.contains(event.relatedTarget)) {
                target.classList.remove('is-dnd-over');
            }
        });

        document.addEventListener('drop', function (event) {
            var target = event.target.closest('[data-drop-folder], [data-drop-section]');
            if (!target || !isTreeDrag(event)) {
                return;
            }
            // Блокировка drop в зону ARCHIVE
            if (isDocumentDrag(event) && isArchiveZone(target)) {
                showDndToast('Нельзя переместить файл в архив', true);
                event.preventDefault();
                return;
            }
            event.preventDefault();
            event.stopPropagation();

            var transfer = event.dataTransfer;
            var folderRaw = transfer.getData('application/x-sod-folder-id') || dragFolderId;
            var documentRaw = transfer.getData('application/x-sod-document-id') ||
                dragDocumentId ||
                transfer.getData('text/plain');
            var targetName = target.dataset.dropName || 'папку';
            var root = panel();
            var copyRequested = event.ctrlKey || event.metaKey;
            clearDropHighlights();

            if (!root) {
                return;
            }

            if (folderRaw) {
                var folderId = numericEntityId(folderRaw);
                if (!folderId) {
                    showDndToast('Не удалось определить папку для перемещения', true);
                    return;
                }
                if (target.dataset.dropFolder === folderId) {
                    return;
                }
                var folderBody = new FormData();
                if (target.dataset.dropFolder) {
                    folderBody.append('parent_id', target.dataset.dropFolder);
                } else if (target.dataset.dropSection) {
                    folderBody.append('section_id', target.dataset.dropSection);
                }
                showDndToast('Перемещаем папку в ' + targetName + '…', false);
                postMoveRequest(
                    apiUrl(root.dataset.folderMoveUrlTemplate, folderId),
                    folderBody,
                    function () {
                        return 'Папка перемещена в ' + targetName;
                    },
                    {reloadTree: true}
                );
                return;
            }

            var documentId = numericEntityId(documentRaw);
            if (!documentId) {
                showDndToast('Не удалось определить документ для перемещения', true);
                return;
            }

            var body = new FormData();
            if (target.dataset.dropFolder) {
                body.append('folder_id', target.dataset.dropFolder);
            } else if (target.dataset.dropSection) {
                body.append('section_id', target.dataset.dropSection);
            }

            var verb = copyRequested ? 'Копируем' : 'Перемещаем';
            showDndToast(verb + ' документ в ' + targetName + '…', false);
            postMoveRequest(
                apiUrl(
                    copyRequested
                        ? root.dataset.copyUrlTemplate
                        : root.dataset.moveUrlTemplate,
                    documentId
                ),
                body,
                function (data) {
                    return copyRequested
                        ? 'Создана копия: ' + (data.code || 'документ')
                        : 'Документ перемещён в ' + targetName;
                }
            );
        });
    }

    /* ---------- Tables helpers ---------- */

    function highlightNewRow() {
        var row = document.querySelector('#documentsTable tr.doc-row-new');
        if (!row) {
            return;
        }
        row.scrollIntoView({block: 'center', behavior: 'smooth'});
        window.setTimeout(function () {
            row.classList.remove('doc-row-new');
        }, 5000);
    }

    function hideBootstrapDropdowns(exceptButton) {
        if (!window.bootstrap) {
            return;
        }
        document.querySelectorAll('[data-bs-toggle="dropdown"]').forEach(function (button) {
            if (button === exceptButton) {
                return;
            }
            var instance = window.bootstrap.Dropdown.getInstance(button);
            if (instance) {
                instance.hide();
            }
        });
    }

    function closeDocumentMenu() {
        var menu = document.getElementById('docRowMenu');
        if (!menu) {
            return;
        }
        menu.classList.remove('show');
        menu.setAttribute('aria-hidden', 'true');
        delete menu.dataset.documentId;
        delete menu.dataset.documentUrl;
        delete menu.dataset.shareUrl;
        delete menu.dataset.deleteUrl;
        delete menu.dataset.documentCode;
    }

    function openDocumentMenu(row, x, y) {
        var menu = document.getElementById('docRowMenu');
        if (!menu) {
            return;
        }
        menu.dataset.documentId = row.dataset.documentId || '';
        menu.dataset.documentUrl = row.dataset.documentUrl || '';
        menu.dataset.shareUrl = row.dataset.shareUrl || '';
        menu.dataset.deleteUrl = row.dataset.deleteUrl || '';
        menu.dataset.documentCode = row.dataset.documentCode || '';
        menu.classList.add('show');
        menu.setAttribute('aria-hidden', 'false');

        var width = menu.offsetWidth || 240;
        var height = menu.offsetHeight || 220;
        var left = Math.max(8, Math.min(x, window.innerWidth - width - 8));
        var top = Math.max(8, Math.min(y, window.innerHeight - height - 8));
        menu.style.left = left + 'px';
        menu.style.top = top + 'px';
    }

    function copyText(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text);
        }
        return new Promise(function (resolve, reject) {
            var textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            try {
                document.execCommand('copy');
                resolve();
            } catch (error) {
                reject(error);
            } finally {
                textarea.remove();
            }
        });
    }

    function runDocumentMenuAction(action, menu) {
        var documentId = menu.dataset.documentId;
        var documentUrl = menu.dataset.documentUrl;
        var shareUrl = menu.dataset.shareUrl;
        var deleteUrl = menu.dataset.deleteUrl;
        var code = menu.dataset.documentCode || 'документ';
        closeDocumentMenu();

        if (action === 'open') {
            window.location.href = documentUrl;
            return;
        }
        if (action === 'versions') {
            window.location.href = documentUrl + '#versions';
            return;
        }
        if (action === 'share') {
            fetch(shareUrl, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
                .then(function (response) {
                    return response.json().then(function (data) {
                        if (!response.ok) {
                            throw new Error(data.error || 'Не удалось создать ссылку');
                        }
                        return data;
                    });
                })
                .then(function (data) {
                    return copyText(data.url).then(function () {
                        showDndToast('Ссылка скопирована', false);
                    });
                })
                .catch(function (error) {
                    showDndToast(error.message, true);
                });
            return;
        }
        if (action === 'move') {
            var modalElement = document.getElementById('documentMoveModal');
            var moveId = document.getElementById('documentMoveId');
            var moveFolder = document.getElementById('documentMoveFolder');
            if (!modalElement || !window.bootstrap) {
                return;
            }
            if (moveId) {
                moveId.value = documentId;
            }
            if (moveFolder) {
                moveFolder.value = '';
            }
            window.bootstrap.Modal.getOrCreateInstance(modalElement).show();
            return;
        }
        if (action === 'delete') {
            if (!window.confirm('Удалить документ «' + code + '» в корзину?')) {
                return;
            }
            fetch(deleteUrl, {
                method: 'POST',
                headers: {'X-CSRFToken': csrfToken()}
            }).then(function (response) {
                return response.json().then(function (data) {
                    if (!response.ok) {
                        throw new Error(data.error || 'Не удалось удалить документ');
                    }
                    return data;
                });
            }).then(function () {
                showDndToast('Документ отправлен в корзину', false);
                refreshDocumentsPanel();
            }).catch(function (error) {
                showDndToast(error.message, true);
            });
        }
    }

    var treeContextMenu = null;

    function closeTreeContextMenu() {
        if (treeContextMenu) {
            treeContextMenu.remove();
            treeContextMenu = null;
        }
    }

    function treeMenuItem(label, options) {
        options = options || {};
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'dropdown-item' + (options.danger ? ' text-danger' : '');
        button.textContent = label;
        if (options.title) {
            button.title = options.title;
        }
        if (options.disabled) {
            button.disabled = true;
        }
        Object.keys(options.attrs || {}).forEach(function (key) {
            button.setAttribute(key, options.attrs[key]);
        });
        if (options.onClick && !options.disabled) {
            button.addEventListener('click', options.onClick);
        }
        return button;
    }

    function openTreeFolderForm(url, bodyId, modalId) {
        if (!url) {
            return;
        }
        bodyId = (bodyId || '').replace(/^#/, '');
        modalId = (modalId || '').replace(/^#/, '');
        fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
            .then(function (response) { return response.text(); })
            .then(function (html) {
                var body = document.getElementById(bodyId);
                var modal = document.getElementById(modalId);
                if (!body || !modal) {
                    return;
                }
                body.innerHTML = html;
                initGostFields();
                if (window.bootstrap) {
                    window.bootstrap.Modal.getOrCreateInstance(modal).show();
                }
            })
            .catch(function () {
                showDndToast('Не удалось открыть форму', true);
            });
    }

    function deleteTreeFolder(url, count) {
        var message = 'Переместить папку в корзину?';
        if (count) {
            message = 'В папке ' + count + ' файл(ов). Они также будут перемещены в корзину. Продолжить?';
        }
        if (!url || !window.confirm(message)) {
            return;
        }
        var form = document.createElement('form');
        form.method = 'post';
        form.action = url;
        var token = document.createElement('input');
        token.type = 'hidden';
        token.name = 'csrfmiddlewaretoken';
        token.value = csrfToken();
        form.appendChild(token);
        document.body.appendChild(form);
        form.submit();
    }

    function startTreeUpload(target) {
        pendingUploadTarget = {
            sectionId: target.getAttribute('data-upload-section') || '',
            folderId: target.getAttribute('data-upload-folder') || '',
            name: target.getAttribute('data-upload-name') || 'выбранную папку'
        };
        var input = document.getElementById('fileInput');
        if (input) {
            input.value = '';
            input.click();
        }
    }

    function showTreeContextMenu(event, target) {
        closeTreeContextMenu();
        var menu = document.createElement('div');
        menu.className = 'dropdown-menu show tree-context-menu';
        var context = target.getAttribute('data-tree-context');
        var manage = target.getAttribute('data-can-manage') === '1';
        var isSection = context === 'section';
        var canCreateAttr = target.getAttribute('data-can-create-subfolder');
        var canCreate = canCreateAttr === null ? manage : canCreateAttr === '1';

        if (canCreate) {
            menu.appendChild(treeMenuItem(
                isSection ? 'Создать папку' : 'Создать подпапку',
                {
                    onClick: function () {
                        openTreeFolderForm(
                            target.getAttribute('data-create-folder-url'),
                            'folderCreateModalBody',
                            'folderCreateModal'
                        );
                    }
                }
            ));
        }

        if (manage) {
            menu.appendChild(treeMenuItem(
                isSection ? 'Переименовать раздел' : 'Переименовать',
                isSection ? {
                    attrs: {
                        'data-section-rename': '',
                        'data-section-id': target.getAttribute('data-section-id') || '',
                        'data-section-name': target.getAttribute('data-section-name') || '',
                        'data-section-code': target.getAttribute('data-section-code') || '',
                        'data-section-url': target.getAttribute('data-rename-url') || ''
                    }
                } : {
                    onClick: function () {
                        openTreeFolderForm(
                            target.getAttribute('data-rename-url'),
                            'folderRenameModalBody',
                            'folderRenameModal'
                        );
                    }
                }
            ));
            if (!isSection) {
                menu.appendChild(treeMenuItem('Переместить', {
                    attrs: {
                        'data-folder-move': '',
                        'data-folder-id': target.getAttribute('data-folder-id') || '',
                        'data-folder-move-url': target.getAttribute('data-move-url') || ''
                    }
                }));
            }
            menu.appendChild(treeMenuItem('Удалить', {
                danger: true,
                disabled: isSection,
                title: isSection ? 'Удаление раздела пока недоступно' : '',
                onClick: isSection ? null : function () {
                    deleteTreeFolder(
                        target.getAttribute('data-delete-url'),
                        target.getAttribute('data-delete-count') || ''
                    );
                }
            }));
        }

        var upload = document.createElement('button');
        upload.type = 'button';
        upload.className = 'dropdown-item';
        upload.textContent = 'Загрузить документ сюда';
        upload.setAttribute('data-upload-section', isSection
            ? (target.getAttribute('data-section-id') || target.getAttribute('data-upload-section-id') || '')
            : (target.getAttribute('data-section-id') || ''));
        upload.setAttribute('data-upload-folder', isSection ? '' : (target.getAttribute('data-folder-id') || ''));
        upload.setAttribute('data-upload-name', isSection
            ? ('раздел ' + (target.getAttribute('data-section-code') || ''))
            : ('папку ' + (target.getAttribute('data-folder-name') || '')));
        upload.addEventListener('click', function () {
            startTreeUpload(upload);
            closeTreeContextMenu();
        });
        menu.appendChild(upload);

        menu.addEventListener('click', function (event) {
            if (event.target.closest('.dropdown-item')) {
                window.setTimeout(closeTreeContextMenu, 0);
            }
        });

        if (event.clientX || event.clientY) {
            menu.style.left = Math.min(event.clientX, window.innerWidth - 240) + 'px';
            menu.style.top = Math.min(event.clientY, window.innerHeight - 220) + 'px';
        }
        document.body.appendChild(menu);
        treeContextMenu = menu;
    }

    function initTreeContextMenu() {
        document.addEventListener('contextmenu', function (event) {
            var target = event.target.closest('[data-tree-context]');
            if (!target) {
                return;
            }
            event.preventDefault();
            showTreeContextMenu(event, target);
        });

        document.querySelectorAll('[data-tree-context]').forEach(function (target) {
            var timer = null;
            var startX = 0;
            var startY = 0;
            target.addEventListener('touchstart', function (event) {
                if (event.touches.length !== 1) {
                    return;
                }
                startX = event.touches[0].clientX;
                startY = event.touches[0].clientY;
                timer = window.setTimeout(function () {
                    var synthetic = {clientX: startX, clientY: startY};
                    showTreeContextMenu(synthetic, target);
                    timer = null;
                }, 600);
            }, {passive: true});
            target.addEventListener('touchmove', function (event) {
                if (!timer) {
                    return;
                }
                var touch = event.touches[0];
                if (Math.abs(touch.clientX - startX) > 8 || Math.abs(touch.clientY - startY) > 8) {
                    window.clearTimeout(timer);
                    timer = null;
                }
            }, {passive: true});
            target.addEventListener('touchend', function () {
                if (timer) {
                    window.clearTimeout(timer);
                    timer = null;
                }
            });
        });

        document.addEventListener('click', function (event) {
            if (treeContextMenu && !treeContextMenu.contains(event.target)) {
                closeTreeContextMenu();
            }
        });
        window.addEventListener('scroll', closeTreeContextMenu, true);
        window.addEventListener('resize', closeTreeContextMenu);
    }

    function initContextMenus() {
        // Один делегированный обработчик: работает и для старых папок,
        // и для папок/строк, появившихся после HTMX-обновления.
        document.addEventListener('contextmenu', function (event) {
            var folderRow = event.target.closest('[data-folder-row], .folder-row');
            if (!folderRow) {
                var folderNode = event.target.closest('[data-folder-id]');
                if (folderNode) {
                    folderRow = folderNode.matches('[data-folder-row], .folder-row')
                        ? folderNode
                        : folderNode.querySelector('[data-folder-row], .folder-row');
                }
            }
            if (folderRow) {
                var button = folderRow.querySelector('[data-folder-menu], .folder-menu-btn');
                if (button && window.bootstrap) {
                    event.preventDefault();
                    event.stopPropagation();
                    closeDocumentMenu();
                    hideBootstrapDropdowns(button);
                    window.bootstrap.Dropdown.getOrCreateInstance(button).show();
                    return;
                }
            }

            var documentRow = event.target.closest(
                '#documentsTable tbody tr[data-document-id]'
            );
            if (!documentRow) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            hideBootstrapDropdowns(null);
            openDocumentMenu(documentRow, event.clientX, event.clientY);
        });

        document.addEventListener('click', function (event) {
            var menu = document.getElementById('docRowMenu');
            if (!menu) {
                return;
            }
            var actionButton = event.target.closest('#docRowMenu [data-doc-action]');
            if (actionButton) {
                event.preventDefault();
                runDocumentMenuAction(actionButton.dataset.docAction, menu);
                return;
            }
            if (!event.target.closest('#docRowMenu')) {
                closeDocumentMenu();
            }
        });

        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                closeDocumentMenu();
            }
        });

        window.addEventListener('scroll', closeDocumentMenu, true);

        var moveForm = document.getElementById('documentMoveForm');
        if (moveForm) {
            moveForm.addEventListener('submit', function (event) {
                event.preventDefault();
                var root = panel();
                var documentId = document.getElementById('documentMoveId');
                var targetFolder = document.getElementById('documentMoveFolder');
                var modalElement = document.getElementById('documentMoveModal');
                if (!root || !documentId || !documentId.value) {
                    return;
                }
                var body = new FormData();
                if (targetFolder && targetFolder.value) {
                    body.append('folder_id', targetFolder.value);
                }
                if (modalElement && window.bootstrap) {
                    window.bootstrap.Modal.getOrCreateInstance(modalElement).hide();
                }
                postMoveRequest(
                    apiUrl(root.dataset.moveUrlTemplate, documentId.value),
                    body,
                    function () {
                        return 'Документ перемещён';
                    }
                );
            });
        }
    }

    function refreshColumnFilters() {
        var form = document.getElementById('filtersForm');
        if (!form) {
            return;
        }
        if (window.htmx) {
            window.htmx.trigger(form, 'submit');
        } else {
            form.submit();
        }
    }

    function bindColumnFilter(column, fieldId, kind) {
        var header = document.querySelector(
            'th[data-column="' + column + '"]'
        );
        var hidden = document.getElementById(fieldId);
        if (!header || !hidden || header.querySelector('.th-filter')) {
            return;
        }
        var wrapper = document.createElement('span');
        wrapper.className = 'th-filter dropdown';
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'th-filter-btn';
        button.setAttribute('data-bs-toggle', 'dropdown');
        button.setAttribute('aria-expanded', 'false');
        button.title = 'Фильтр';
        button.innerHTML = '<i class="bi bi-funnel" aria-hidden="true"></i>';
        var menu = document.createElement('div');
        menu.className = 'dropdown-menu p-2';
        var control;
        var timer = null;

        function syncActive() {
            button.classList.toggle('is-active', Boolean(hidden.value));
        }

        function scheduleRefresh() {
            syncActive();
            if (timer) {
                window.clearTimeout(timer);
            }
            timer = window.setTimeout(refreshColumnFilters, kind === 'text' ? 350 : 0);
        }

        if (kind === 'select') {
            control = document.createElement('select');
            control.className = 'form-select form-select-sm';
            Array.from(hidden.options).forEach(function (option) {
                var cloned = document.createElement('option');
                cloned.value = option.value;
                cloned.textContent = option.textContent;
                control.appendChild(cloned);
            });
            control.value = hidden.value || '';
            control.addEventListener('change', function () {
                hidden.value = control.value;
                scheduleRefresh();
            });
        } else {
            control = document.createElement('input');
            control.type = 'search';
            control.className = 'form-control form-control-sm';
            control.placeholder = header.textContent.trim() || 'Поиск';
            control.value = hidden.value || '';
            control.addEventListener('input', function () {
                hidden.value = control.value;
                scheduleRefresh();
            });
            control.addEventListener('keydown', function (event) {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    refreshColumnFilters();
                }
            });
        }

        menu.appendChild(control);
        wrapper.appendChild(button);
        wrapper.appendChild(menu);
        header.appendChild(wrapper);
        syncActive();
    }

    function initColumnFilters() {
        bindColumnFilter('section', 'id_section', 'select');
        bindColumnFilter('doc_type', 'id_doc_type', 'select');
        bindColumnFilter('suitability', 'id_suitability_code', 'select');
        bindColumnFilter('internal-status', 'id_internal_status', 'select');
        bindColumnFilter('code', 'id_code', 'text');
        bindColumnFilter('name', 'id_name', 'text');

        var search = document.getElementById('globalSearch');
        var hiddenSearch = document.getElementById('quickFilter');
        if (!search || !hiddenSearch || search.dataset.filterReady === '1') {
            return;
        }
        search.dataset.filterReady = '1';
        var timer = null;
        search.addEventListener('input', function () {
            hiddenSearch.value = search.value;
            if (timer) {
                window.clearTimeout(timer);
            }
            timer = window.setTimeout(refreshColumnFilters, 350);
        });
        search.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                refreshColumnFilters();
            }
        });
    }

    function initDocumentsPanel() {
        var table = documentsTable();
        if (table) {
            table.querySelectorAll('tbody a').forEach(function (link) {
                link.setAttribute('draggable', 'false');
            });
        }
        initColumnOrder();
        initColumnFilters();
        updateBulkBar();
        updateUploadTarget();
        highlightNewRow();
    }

    function initHtmx() {
        document.body.addEventListener('htmx:afterSwap', function (event) {
            if (document.getElementById('documentsPanel')) {
                initDocumentsPanel();
            }
        });
        document.body.addEventListener('htmx:beforeSwap', function (event) {
            if (event.detail && event.detail.target &&
                    event.detail.target.id === 'documentsPanel') {
                showDropOverlay(false);
            }
        });
        document.body.addEventListener('htmx:afterRequest', function (event) {
            if (event.detail && event.detail.target &&
                    event.detail.target.id === 'documentsPanel') {
                updateBulkBar();
            }
        });
    }

    /* ---------- Волна 3: зоны ISO 19650 (сегмент-контрол + workflow) ---------- */

    function initZoneWorkflow() {
        var panel = document.getElementById('documentsPanel');
        if (!panel) {
            return;
        }

        // Клик по чипу зоны: подставить zone в скрытый input формы фильтров
        // и отправить её через htmx — фильтры раздела/типа/поиска сохраняются.
        document.addEventListener('click', function (event) {
            var chip = event.target.closest('[data-role="zone-chip"]');
            if (chip) {
                event.preventDefault();
                var form = document.getElementById('filtersForm');
                var input = form && form.querySelector('[data-role="zone-input"]');
                if (form && input) {
                    input.value = chip.getAttribute('data-zone') || '';
                    if (window.htmx) {
                        window.htmx.trigger(form, 'submit');
                    } else {
                        form.submit();
                    }
                }
                return;
            }

            var btn = event.target.closest('[data-workflow-action]');
            if (!btn) {
                return;
            }
            event.preventDefault();
            applyWorkflowAction(btn);
        });
    }

    function applyWorkflowAction(btn) {
        var action = btn.getAttribute('data-workflow-action');
        var url = btn.getAttribute('data-workflow-url');
        if (!action || !url || btn.dataset.busy === '1') {
            return;
        }
        btn.dataset.busy = '1';
        btn.classList.add('is-busy');

        var body = new URLSearchParams();
        body.set('action', action);

        fetch(url, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken(),
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: body
        })
            .then(function (response) {
                return response.json().then(function (data) {
                    return { ok: response.ok, data: data };
                });
            })
            .then(function (result) {
                if (!result.ok || result.data.status !== 'ok') {
                    showToast(result.data && result.data.error
                        ? result.data.error
                        : 'Не удалось выполнить действие');
                    return;
                }
                updateDocumentRow(result.data);
                updateDocumentCard(result.data);
                updateZoneCounts(result.data.zone_code);
            })
            .catch(function () {
                showToast('Сеть недоступна — попробуйте ещё раз');
            })
            .finally(function () {
                delete btn.dataset.busy;
                btn.classList.remove('is-busy');
            });
    }

    function renderWorkflowButtons(cell, actions, documentId) {
        if (!cell) {
            return;
        }
        if (!actions || !actions.length) {
            cell.innerHTML = '<span class="text-muted">—</span>';
            return;
        }
        var buttons = actions.map(function (item) {
            var url = item.url || urlForWorkflow(documentId);
            return '<button type="button" ' +
                'class="wf-btn wf-btn-text btn btn-sm btn-outline-secondary me-1 mb-1" ' +
                'data-workflow-action="' + escapeHtml(item.code) + '" ' +
                'data-workflow-url="' + escapeHtml(url) + '" ' +
                'title="' + escapeHtml(item.label) + '" ' +
                'aria-label="' + escapeHtml(item.label) + '">' +
                escapeHtml(item.label) + '</button>';
        });
        cell.innerHTML = '<span class="wf-actions" data-role="workflow-actions">' +
            buttons.join('') + '</span>';
    }

    function updateDocumentCard(data) {
        var card = document.querySelector('[data-viewer-actions]');
        if (!card) {
            return;
        }
        var suitability = document.querySelector('[data-viewer-chip="suitability"]');
        if (suitability) {
            suitability.textContent = data.suitability_display || '';
            suitability.title = data.suitability_display || '';
        }
        var internal = document.querySelector('[data-viewer-chip="internal"]');
        if (internal) {
            internal.textContent = data.internal_display || '';
            internal.title = data.internal_display || '';
        }
        renderWorkflowButtons(card, data.actions, data.document_id);
    }

    function updateDocumentRow(data) {
        var row = document.querySelector(
            'tr[data-document-id="' + data.document_id + '"]'
        );
        if (!row) {
            return;
        }
        if (data.zone_code) {
            row.dataset.zone = data.zone_code;
            row.dataset.zoneName = data.zone_name || '';
        }
        var zoneCell = row.querySelector('[data-column="zone"]');
        if (zoneCell) {
            var icons = {
                WIP: 'bi-pencil-square',
                SHARED: 'bi-people',
                PUBLISHED: 'bi-shield-check',
                ARCHIVE: 'bi-lock'
            };
            var colors = {
                WIP: 'text-primary',
                SHARED: 'text-warning',
                PUBLISHED: 'text-success',
                ARCHIVE: 'text-secondary'
            };
            zoneCell.innerHTML = data.zone_code
                ? '<i class="bi ' + (icons[data.zone_code] || 'bi-folder') + ' ' +
                  (colors[data.zone_code] || '') + ' me-1" aria-hidden="true"></i>' +
                  escapeHtml(data.zone_name || '')
                : '—';
        }

        var statusCell = row.querySelector('[data-column="status"]');
        if (statusCell) {
            var badge = STATUS_BADGE_CLASSES[data.status_code] || 'doc-badge-draft';
            statusCell.innerHTML = '<span class="doc-badge ' + badge + '">' +
                escapeHtml(data.status_display || '') + '</span>';
        }

        var suitabilityCell = row.querySelector('[data-column="suitability"]');
        if (suitabilityCell) {
            suitabilityCell.innerHTML = '<span class="doc-suitability" title="' +
                escapeHtml(data.suitability_display || '') + '">' +
                escapeHtml(data.suitability_display || '') + '</span>';
        }

        var internalCell = row.querySelector('[data-column="internal-status"]');
        if (internalCell) {
            internalCell.innerHTML = data.zone_code === 'WIP'
                ? '<span class="doc-internal-status" title="' +
                  escapeHtml(data.internal_display || '') + '">' +
                  escapeHtml(data.internal_display || '') + '</span>'
                : '—';
        }

        var issuesCell = row.querySelector('[data-column="issues"]');
        if (issuesCell) {
            var openIssues = Number(data.open_issues || 0);
            issuesCell.innerHTML = openIssues
                ? '<span class="badge bg-danger" title="Открытые замечания">' +
                  openIssues + '</span>'
                : '0';
        }

        var actionsCell = row.querySelector('[data-column="actions"]');
        if (actionsCell && data.actions) {
            renderWorkflowButtons(actionsCell, data.actions, data.document_id);
        }

        row.classList.remove('doc-row-flash');
        void row.offsetWidth; /* reflow — перезапуск анимации */
        row.classList.add('doc-row-flash');
        setTimeout(function () {
            row.classList.remove('doc-row-flash');
        }, 1600);
    }

    var STATUS_BADGE_CLASSES = {
        approved: 'doc-badge-approved',
        in_review: 'doc-badge-review',
        archived: 'doc-badge-archived',
        draft: 'doc-badge-draft'
    };

    function urlForWorkflow(documentId) {
        return '/documents/' + documentId + '/workflow/';
    }

    function updateZoneCounts(activeZone) {
        // Точные счётчики пересчитает сервер при следующем запросе панели;
        // здесь — мгновенный локальный сдвиг ±1 для обратной связи.
        var chips = document.querySelectorAll('[data-zone-count]');
        chips.forEach(function (el) {
            var zone = el.getAttribute('data-zone-count') || '';
            var value = parseInt(el.textContent, 10);
            if (isNaN(value)) {
                return;
            }
            if (zone === activeZone) {
                el.textContent = String(value + 1);
            }
        });
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    var TOAST_TIMEOUT = null;

    function showToast(message) {
        var toast = document.getElementById('wfToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'wfToast';
            toast.className = 'wf-toast';
            toast.setAttribute('role', 'status');
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.classList.add('is-visible');
        clearTimeout(TOAST_TIMEOUT);
        TOAST_TIMEOUT = setTimeout(function () {
            toast.classList.remove('is-visible');
        }, 3500);
    }

    function initFolderMove() {
        var modalElement = document.getElementById('folderMoveModal');
        var form = document.getElementById('folderMoveForm');
        var target = document.getElementById('folderMoveTarget');
        var folderIdInput = document.getElementById('folderMoveId');
        if (!modalElement || !form || !target || !folderIdInput) {
            return;
        }
        var moveUrl = '';

        document.addEventListener('click', function (event) {
            var trigger = event.target.closest('[data-folder-move]');
            if (!trigger) {
                return;
            }
            event.preventDefault();
            moveUrl = trigger.getAttribute('data-folder-move-url') || '';
            folderIdInput.value = trigger.getAttribute('data-folder-id') || '';
            target.value = '';
            if (window.bootstrap) {
                window.bootstrap.Modal.getOrCreateInstance(modalElement).show();
            }
        });

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            if (!moveUrl || !folderIdInput.value || !target.value) {
                return;
            }
            var body = new FormData();
            if (target.value.indexOf('section:') === 0) {
                body.append('section_id', target.value.slice('section:'.length));
            } else {
                body.append('parent_id', target.value.slice('folder:'.length));
            }
            fetch(moveUrl, {
                method: 'POST',
                headers: {'X-CSRFToken': csrfToken()},
                body: body,
                credentials: 'same-origin'
            }).then(function (response) {
                return response.json().then(function (data) {
                    return {ok: response.ok, data: data};
                });
            }).then(function (result) {
                if (!result.ok || result.data.status !== 'ok') {
                    showDndToast(
                        (result.data && result.data.error) ||
                        'Не удалось переместить папку',
                        true
                    );
                    return;
                }
                window.location.reload();
            }).catch(function () {
                showDndToast('Не удалось переместить папку', true);
            });
        });
    }

    function initSectionAccordion() {
        document.querySelectorAll('[data-section-toggle]').forEach(function (button) {
            var sectionId = button.getAttribute('data-section-toggle');
            var content = document.querySelector(
                '[data-section-content="' + sectionId + '"]'
            );
            var storageKey = 'section-open-' + sectionId;
            var open = localStorage.getItem(storageKey) !== '0';

            function apply() {
                if (content) {
                    content.classList.toggle('d-none', !open);
                }
                button.setAttribute('aria-expanded', open ? 'true' : 'false');
                button.classList.toggle('collapsed', !open);
                button.title = open ? 'Свернуть раздел' : 'Развернуть раздел';
            }

            apply();
            button.addEventListener('click', function (event) {
                event.preventDefault();
                event.stopPropagation();
                open = !open;
                localStorage.setItem(storageKey, open ? '1' : '0');
                apply();
            });
        });
    }

    function updateGostForm(form) {
        if (!form) {
            return;
        }
        var preview = form.querySelector('[data-gost-preview]');
        if (!preview) {
            return;
        }
        var kind = form.getAttribute('data-gost-kind') || '';
        var base = form.getAttribute('data-gost-base') || '';
        var section = form.getAttribute('data-gost-section') || '';
        var parentPrefix = form.getAttribute('data-gost-parent') || '';
        var markInput = form.querySelector('[data-gost-mark]');
        var codeInput = form.querySelector('[data-gost-folder-code]');
        if (kind === 'section') {
            var mark = markInput ? markInput.value.trim() : '';
            preview.value = [base, mark].filter(Boolean).join('-');
        } else {
            var code = codeInput ? codeInput.value.trim() : '';
            preview.value = parentPrefix
                ? [parentPrefix, code].filter(Boolean).join('-')
                : [base, section, code].filter(Boolean).join('-');
        }
    }

    function initGostFields() {
        document.querySelectorAll('form[data-gost-kind]').forEach(updateGostForm);
    }

    document.addEventListener('input', function (event) {
        var markInput = event.target.closest('[data-gost-mark]');
        if (markInput) {
            markInput.value = markInput.value
                .toUpperCase()
                .replace(/[^А-ЯЁ]/g, '')
                .slice(0, 3);
            updateGostForm(markInput.closest('form'));
            return;
        }
        var codeInput = event.target.closest('[data-gost-folder-code]');
        if (codeInput) {
            codeInput.value = codeInput.value.replace(/\D/g, '').slice(0, 10);
            updateGostForm(codeInput.closest('form'));
        }
    });

    function initSectionRename() {
        var modalElement = document.getElementById('sectionRenameModal');
        var form = document.getElementById('sectionRenameForm');
        var nameInput = document.getElementById('sectionRenameName');
        var codeInput = document.getElementById('sectionRenameCode');
        if (!modalElement || !form || !nameInput || !codeInput) {
            return;
        }
        document.addEventListener('click', function (event) {
            var trigger = event.target.closest('[data-section-rename]');
            if (!trigger) {
                return;
            }
            event.preventDefault();
            form.action = trigger.getAttribute('data-section-url') || '';
            nameInput.value = trigger.getAttribute('data-section-name') || '';
            codeInput.value = trigger.getAttribute('data-section-code') || '';
            updateGostForm(form);
            if (window.bootstrap) {
                window.bootstrap.Modal.getOrCreateInstance(modalElement).show();
            }
        });
    }

    function initFolderReorder() {
        if (!window.Sortable) {
            return;
        }
        document.querySelectorAll('[data-folder-sortable]').forEach(function (list) {
            if (list.dataset.sortableReady === '1') {
                return;
            }
            list.dataset.sortableReady = '1';
            window.Sortable.create(list, {
                animation: 150,
                draggable: '.folder-node',
                filter: 'button, a, input, select, .dropdown-menu',
                preventOnFilter: false,
                onEnd: function () {
                    var ids = Array.from(list.children)
                        .filter(function (node) {
                            return node.classList.contains('folder-node');
                        })
                        .map(function (node) {
                            return node.getAttribute('data-folder-id');
                        })
                        .filter(Boolean);
                    var url = list.getAttribute('data-folder-reorder-url');
                    if (!url || !ids.length) {
                        return;
                    }
                    var body = new FormData();
                    ids.forEach(function (id) {
                        body.append('ids', id);
                    });
                    fetch(url, {
                        method: 'POST',
                        headers: {'X-CSRFToken': csrfToken()},
                        body: body,
                        credentials: 'same-origin'
                    }).then(function (response) {
                        if (!response.ok) {
                            throw new Error('reorder');
                        }
                        window.location.reload();
                    }).catch(function () {
                        showDndToast('Не удалось сохранить порядок папок', true);
                    });
                }
            });
        });
    }

    function init() {
        initSidebar();
        initBulkSelection();
        initUpload();
        initDocumentDragAndDrop();
        initContextMenus();
        initTreeContextMenu();
        initDocumentsPanel();
        initZoneWorkflow();
        initFolderMove();
        initSectionRename();
        initSectionAccordion();
        initGostFields();
        document.body.addEventListener('htmx:afterSwap', initGostFields);
        initFolderReorder();
        initHtmx();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
