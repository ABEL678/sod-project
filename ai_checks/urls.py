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
]
