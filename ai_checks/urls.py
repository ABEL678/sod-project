"""URL ИИ-контура (Ф1). Гейт AI_CHECKS_ENABLED — во вьюхах (404)."""

from django.urls import path

from ai_checks import views

app_name = 'ai_checks'

urlpatterns = [
    path(
        'document/<int:document_id>/panel/',
        views.panel, name='panel',
    ),
    path(
        'document/<int:document_id>/start/',
        views.start_check, name='start',
    ),
    path('finding/<int:pk>/confirm/', views.confirm_finding, name='confirm'),
    path('finding/<int:pk>/reject/', views.reject_finding, name='reject'),
    path('finding/<int:pk>/preview/', views.finding_preview, name='preview'),
    # Ф2: RAG по внутренним документам (вкладка «ИИ-поиск»).
    path('project/<int:project_id>/rag/', views.rag_panel, name='rag_panel'),
    path(
        'project/<int:project_id>/rag/search/',
        views.rag_search, name='rag_search',
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
    # База знаний проекта (UX-аудит stage 2).
    path(
        'project/<int:project_id>/kb/',
        views.kb_panel, name='kb_panel',
    ),
    path(
        'project/<int:project_id>/kb/table/',
        views.kb_table, name='kb_table',
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
]
