document.addEventListener('DOMContentLoaded', function() {
    const divider = document.getElementById('drag-divider');
    const pdfPanel = document.getElementById('pdf-panel');
    const issuesPanel = document.getElementById('issues-panel');
    let isDragging = false;

    if (!divider || !pdfPanel || !issuesPanel) {
        return;
    }

    divider.addEventListener('mousedown', function(e) {
        isDragging = true;
        document.body.style.cursor = 'col-resize';
        e.preventDefault();
    });

    document.addEventListener('mousemove', function(e) {
        if (!isDragging) return;
        const containerRect = document.getElementById('main-splitter').getBoundingClientRect();
        const newWidth = e.clientX - containerRect.left;

        if (newWidth > 300 && newWidth < containerRect.width - 300) {
            const percent = (newWidth / containerRect.width) * 100;
            pdfPanel.style.width = percent + '%';
            issuesPanel.style.width = (100 - percent) + '%';
        }
    });

    document.addEventListener('mouseup', function() {
        isDragging = false;
        document.body.style.cursor = 'default';
    });
});
