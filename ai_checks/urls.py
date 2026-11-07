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
    path('report/<int:pk>/json/', views.report_json, name='report_json'),
]
