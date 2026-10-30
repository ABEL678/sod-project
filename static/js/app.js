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

    async function handleFiles(files) {
        if (uploadInProgress) {
            return;
        }
        var root = panel();
        var target = currentUploadTarget();
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
            if (event.target.id === 'fileInput' && event.target.files.length) {
                handleFiles(event.target.files);
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
        if (window.htmx && panel()) {
            window.htmx.ajax('GET', window.location.href, {
                target: '#documentsPanel',
                swap: 'outerHTML'
            });
            return;
        }
        window.location.reload();
    }

    function apiUrl(template, entityId) {
        return String(template || '').replace(
            '/0/',
            '/' + encodeURIComponent(entityId) + '/'
        );
    }

    function postMoveRequest(url, body, successMessage) {
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
            refreshDocumentsPanel();
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
                    }
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

    function initDocumentsPanel() {
        var table = documentsTable();
        if (table) {
            table.querySelectorAll('tbody a').forEach(function (link) {
                link.setAttribute('draggable', 'false');
            });
        }
        initColumnOrder();
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

    function init() {
        initSidebar();
        initBulkSelection();
        initUpload();
        initDocumentDragAndDrop();
        initContextMenus();
        initDocumentsPanel();
        initHtmx();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
