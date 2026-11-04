(function () {
    'use strict';

    function csrfToken() {
        var input = document.querySelector('[name=csrfmiddlewaretoken]');
        if (input) {
            return input.value;
        }
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function showToast(text) {
        var toastEl = document.getElementById('feedbackToast');
        var toastBody = document.getElementById('feedbackToastBody');
        if (!toastEl) {
            window.alert(text);
            return;
        }
        if (toastBody) {
            toastBody.textContent = text;
        }
        if (window.bootstrap && window.bootstrap.Toast) {
            window.bootstrap.Toast.getOrCreateInstance(toastEl, {delay: 4000}).show();
        } else {
            window.alert(text);
        }
    }

    function initSubmitModal() {
        var modalEl = document.getElementById('feedbackModal');
        var form = document.getElementById('feedbackForm');
        if (!modalEl || !form) {
            return;
        }

        var messageInput = document.getElementById('feedbackMessage');
        var messageCount = document.getElementById('feedbackMessageCount');
        var pageUrlInput = document.getElementById('feedbackPageUrl');
        var errorsBox = document.getElementById('feedbackErrors');
        var submitButton = document.getElementById('feedbackSubmit');
        var submitSpinner = document.getElementById('feedbackSpinner');
        var submitLabel = document.getElementById('feedbackSubmitLabel');

        function clearErrors() {
            form.querySelectorAll('[data-feedback-error]').forEach(function (el) {
                el.textContent = '';
                el.classList.add('d-none');
                el.classList.remove('d-block');
            });
            form.querySelectorAll('.is-invalid').forEach(function (el) {
                el.classList.remove('is-invalid');
            });
            if (errorsBox) {
                errorsBox.textContent = '';
                errorsBox.classList.add('d-none');
            }
        }

        function setError(name, messages) {
            var field = form.elements.namedItem(name);
            var error = form.querySelector('[data-feedback-error="' + name + '"]');
            if (!field || !error) {
                return false;
            }
            if (field.classList) {
                field.classList.add('is-invalid');
            }
            error.textContent = messages.join(' ');
            error.classList.remove('d-none');
            error.classList.add('d-block');
            return true;
        }

        function showErrors(errors) {
            clearErrors();
            var generic = [];
            var hasFieldErrors = false;
            Object.keys(errors || {}).forEach(function (name) {
                var messages = errors[name];
                if (!Array.isArray(messages)) {
                    messages = [String(messages)];
                }
                if (name === 'non_field_errors' || name === '__all__') {
                    generic = generic.concat(messages);
                    return;
                }
                if (setError(name, messages)) {
                    hasFieldErrors = true;
                } else {
                    generic = generic.concat(messages);
                }
            });
            if (generic.length && errorsBox) {
                errorsBox.textContent = generic.join(' ');
                errorsBox.classList.remove('d-none');
            } else if (hasFieldErrors && errorsBox) {
                errorsBox.textContent = 'Проверьте выделенные поля формы.';
                errorsBox.classList.remove('d-none');
            }
            return generic;
        }

        function setBusy(busy) {
            if (submitButton) {
                submitButton.disabled = busy;
            }
            if (submitSpinner) {
                submitSpinner.classList.toggle('d-none', !busy);
            }
            if (submitLabel) {
                submitLabel.textContent = busy ? 'Отправка…' : 'Отправить';
            }
        }

        function updateCounter() {
            if (messageCount && messageInput) {
                messageCount.textContent = String(messageInput.value.length);
            }
        }

        if (messageInput) {
            messageInput.addEventListener('input', updateCounter);
        }

        modalEl.addEventListener('show.bs.modal', function () {
            clearErrors();
            if (pageUrlInput) {
                pageUrlInput.value = window.location.href.slice(0, 500);
            }
            updateCounter();
        });

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            clearErrors();
            setBusy(true);

            fetch(form.dataset.submitUrl || form.action, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrfToken(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: new FormData(form),
                credentials: 'same-origin'
            }).then(function (response) {
                return response.json().catch(function () {
                    return {};
                }).then(function (payload) {
                    return {
                        ok: response.ok,
                        status: response.status,
                        payload: payload
                    };
                });
            }).then(function (result) {
                if (result.ok && result.payload.ok) {
                    if (window.bootstrap && window.bootstrap.Modal) {
                        window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                    }
                    form.reset();
                    var category = document.getElementById('feedbackCategory');
                    if (category) {
                        category.value = 'BUG';
                    }
                    if (messageInput) {
                        messageInput.value = '';
                    }
                    updateCounter();
                    showToast('Спасибо! Сообщение отправлено.');
                    return;
                }
                if (
                    result.status === 400
                    && result.payload
                    && result.payload.errors
                ) {
                    var generalErrors = showErrors(result.payload.errors);
                    if (generalErrors.length) {
                        showToast('Ошибка: ' + generalErrors.join(' '));
                    }
                    return;
                }
                showToast('Не удалось отправить, попробуйте позже.');
            }).catch(function () {
                showToast('Не удалось отправить, попробуйте позже.');
            }).finally(function () {
                setBusy(false);
            });
        });
    }

    function initAdminFeedback() {
        var detailModalEl = document.getElementById('feedbackDetailModal');
        var detailSave = document.getElementById('feedbackDetailSave');
        var detailComment = document.getElementById('feedbackDetailComment');
        var detailError = document.getElementById('feedbackDetailError');
        var detailRow = null;

        function clearDetailError() {
            if (detailError) {
                detailError.textContent = '';
                detailError.classList.add('d-none');
            }
        }

        function showDetailError(messages) {
            if (detailError) {
                detailError.textContent = (messages || []).join(' ');
                detailError.classList.remove('d-none');
            }
        }

        function setRowBusy(row, busy, status) {
            row.querySelectorAll('[data-feedback-status]').forEach(function (button) {
                if (busy) {
                    button.disabled = true;
                } else {
                    button.disabled = button.dataset.feedbackStatus === status;
                }
            });
        }

        function updateStatus(row, status, comment, fromDetail) {
            if (!row || !row.dataset.statusUrl) {
                return;
            }

            var body = new FormData();
            if (status) {
                body.append('status', status);
            }
            if (comment) {
                body.append('admin_comment', comment);
            }

            setRowBusy(row, true, row.dataset.currentStatus);
            if (fromDetail && detailSave) {
                detailSave.disabled = true;
            }

            fetch(row.dataset.statusUrl, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrfToken(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: body,
                credentials: 'same-origin'
            }).then(function (response) {
                return response.json().catch(function () {
                    return {};
                }).then(function (payload) {
                    return {
                        ok: response.ok,
                        status: response.status,
                        payload: payload
                    };
                });
            }).then(function (result) {
                if (result.ok && result.payload.ok) {
                    var newStatus = result.payload.status;
                    row.dataset.currentStatus = newStatus;
                    if (comment) {
                        row.dataset.adminComment = comment;
                    }

                    var badge = row.querySelector('[data-feedback-status-badge]');
                    if (badge) {
                        badge.textContent = result.payload.status_display;
                        badge.className = 'cde-badge ' + result.payload.status_class;
                    }
                    var openButton = row.querySelector('[data-feedback-open]');
                    if (openButton) {
                        openButton.dataset.feedbackStatusLabel = (
                            result.payload.status_display
                        );
                        openButton.dataset.feedbackStatusClass = (
                            result.payload.status_class
                        );
                    }
                    setRowBusy(row, false, newStatus);

                    if (fromDetail && detailRow === row) {
                        var detailStatus = document.getElementById('feedbackDetailStatus');
                        if (detailStatus) {
                            detailStatus.textContent = result.payload.status_display;
                            detailStatus.className = 'cde-badge ' + result.payload.status_class;
                        }
                        if (detailComment && comment) {
                            detailComment.value = comment;
                        }
                    }
                    showToast(comment ? 'Комментарий сохранён.' : 'Статус обновлён.');
                    return;
                }

                if (
                    result.status === 400
                    && result.payload
                    && result.payload.errors
                    && fromDetail
                ) {
                    var messages = [];
                    Object.keys(result.payload.errors).forEach(function (name) {
                        messages = messages.concat(result.payload.errors[name]);
                    });
                    showDetailError(messages);
                } else if (result.status === 403) {
                    showToast('Недостаточно прав для изменения обращения.');
                } else {
                    showToast('Не удалось обновить обращение.');
                }
                setRowBusy(row, false, row.dataset.currentStatus);
            }).catch(function () {
                showToast('Не удалось обновить обращение.');
                setRowBusy(row, false, row.dataset.currentStatus);
            }).finally(function () {
                if (fromDetail && detailSave) {
                    detailSave.disabled = false;
                }
            });
        }

        document.addEventListener('click', function (event) {
            if (!event.target || !event.target.closest) {
                return;
            }
            var statusButton = event.target.closest('[data-feedback-status]');
            if (!statusButton) {
                return;
            }
            var row = statusButton.closest('[data-feedback-row]');
            if (row) {
                updateStatus(row, statusButton.dataset.feedbackStatus, '', false);
            }
        });

        if (detailModalEl) {
            detailModalEl.addEventListener('show.bs.modal', function (event) {
                var trigger = event.relatedTarget;
                if (!trigger || !trigger.hasAttribute('data-feedback-open')) {
                    return;
                }
                detailRow = trigger.closest('[data-feedback-row]');
                clearDetailError();

                var message = document.getElementById('feedbackDetailMessage');
                var author = document.getElementById('feedbackDetailAuthor');
                var created = document.getElementById('feedbackDetailCreated');
                var url = document.getElementById('feedbackDetailUrl');
                var category = document.getElementById('feedbackDetailCategory');
                var status = document.getElementById('feedbackDetailStatus');
                var screenshotWrap = document.getElementById('feedbackDetailScreenshotWrap');
                var screenshot = document.getElementById('feedbackDetailScreenshot');

                if (message) {
                    message.textContent = trigger.dataset.feedbackMessage || '';
                }
                if (author) {
                    author.textContent = trigger.dataset.feedbackAuthor || '—';
                }
                if (created) {
                    created.textContent = trigger.dataset.feedbackCreated || '';
                }
                if (url) {
                    url.textContent = trigger.dataset.feedbackUrl || '—';
                }
                if (category) {
                    category.textContent = trigger.dataset.feedbackCategory || '';
                    category.className = (
                        'cde-badge ' + (trigger.dataset.feedbackCategoryClass || '')
                    );
                }
                if (status) {
                    status.textContent = trigger.dataset.feedbackStatusLabel || '';
                    status.className = (
                        'cde-badge ' + (trigger.dataset.feedbackStatusClass || '')
                    );
                }

                var screenshotUrl = trigger.dataset.feedbackScreenshot || '';
                if (screenshot && screenshotWrap) {
                    if (screenshotUrl) {
                        screenshot.src = screenshotUrl;
                        screenshotWrap.classList.remove('d-none');
                    } else {
                        screenshot.removeAttribute('src');
                        screenshotWrap.classList.add('d-none');
                    }
                }

                if (detailComment) {
                    detailComment.value = detailRow
                        ? (detailRow.dataset.adminComment || '')
                        : '';
                }
            });
        }

        if (detailSave) {
            detailSave.addEventListener('click', function () {
                if (!detailRow) {
                    return;
                }
                clearDetailError();
                var comment = detailComment ? detailComment.value.trim() : '';
                updateStatus(detailRow, '', comment, true);
            });
        }
    }

    initSubmitModal();
    initAdminFeedback();
})();
