"""URL ИИ-контура (Ф1). Гейт AI_CHECKS_ENABLED — во вьюхах (404)."""

from django.urls import path

from ai_checks import views

app_name = 'ai_checks'

urlpatterns = [
    # Ф2: RAG по внутренним документам (вкладка «ИИ-поиск»).
    path('project/<int:project_id>/rag/', views.rag_panel, name='rag_panel'),
    path(
        'project/<int:project_id>/rag/search/',
        views.rag_search, name='rag_search',
    ),
    # Треб. 9/10: история ИИ-поиска (поиск, фильтры, пагинация).
    path(
        'project/<int:project_id>/rag/history/',
        views.rag_history, name='rag_history',
    ),
    path(
        'project/<int:project_id>/rag/history/<int:pk>/',
        views.rag_history_item, name='rag_history_item',
    ),
    # UI-аудит (треб. 7.1): групповое удаление из истории (admin/gip).
    path(
        'project/<int:project_id>/rag/history/bulk-delete/',
        views.rag_history_bulk_delete, name='rag_history_bulk_delete',
    ),
    # Ф4: отчёт ГИПу.
    path(
        'project/<int:project_id>/report/',
        views.report_panel, name='report_panel',
    ),
    path(
        'project/<int:project_id>/report/generate/',
        views.report_generate, name='report_generate',
    ),
    path('report/<int:pk>/pdf/', views.report_pdf, name='report_pdf'),
    path('report/<int:pk>/docx/', views.report_docx, name='report_docx'),
    # UI-аудит (треб. 6.3): справка ИИ-поиска в PDF.
    path('rag/<int:pk>/pdf/', views.rag_report_pdf, name='rag_report_pdf'),
    # UI-аудит (треб. 7.2/8.2): удаление запросов истории и отчётов.
    path(
        'project/<int:project_id>/rag/history/<int:pk>/delete/',
        views.rag_history_delete, name='rag_history_delete',
    ),
    path('report/<int:pk>/delete/', views.report_delete, name='report_delete'),
    # База знаний проекта (UX-аудит stage 2 + треб. 5/7).
    path(
        'project/<int:project_id>/kb/',
        views.kb_panel, name='kb_panel',
    ),
    path(
        'project/<int:project_id>/kb/table/',
        views.kb_table, name='kb_table',
    ),
    path(
        'project/<int:project_id>/kb/documents/',
        views.kb_projects_table, name='kb_projects_table',
    ),
    path(
        'project/<int:project_id>/kb/reindex-project/',
        views.kb_reindex_project_view, name='kb_reindex_project',
    ),
    path(
        'project/<int:project_id>/kb/document/<int:document_id>/reindex/',
        views.kb_reindex_document_view, name='kb_reindex_document',
    ),
    path(
        'project/<int:project_id>/kb/upload/',
        views.kb_upload, name='kb_upload',
    ),
    path(
        'project/<int:project_id>/kb/<int:pk>/reindex/',
        views.kb_reindex, name='kb_reindex',
    ),
    path(
        'project/<int:project_id>/kb/<int:pk>/remove/',
        views.kb_disable, name='kb_disable',
    ),
    path(
        'project/<int:project_id>/kb/<int:pk>/chunks/',
        views.kb_chunks, name='kb_chunks',
    ),
    # UI-аудит (треб. 9.2): редактирование названия/типа и мягкое удаление
    # (алиас kb_document_delete → та же вьюха kb_disable, is_active=False).
    path(
        'project/<int:project_id>/kb/<int:pk>/edit/',
        views.kb_document_edit, name='kb_document_edit',
    ),
    path(
        'project/<int:project_id>/kb/<int:pk>/delete/',
        views.kb_disable, name='kb_document_delete',
    ),
]
