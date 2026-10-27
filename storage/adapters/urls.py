from django.urls import path

from storage.adapters import views

app_name = 'storage'

urlpatterns = [
    path('', views.ProjectListView.as_view(), name='project_list'),
    path('projects/<int:pk>/', views.ProjectDetailView.as_view(), name='project_detail'),
    path('projects/<int:project_id>/upload-ajax/', views.DocumentUploadAjaxView.as_view(), name='document_upload_ajax'),
    path('download/<int:version_id>/', views.DocumentDownloadView.as_view(), name='document_download'),
    path('projects/<int:project_id>/export/', views.ExportVedomostView.as_view(), name='export_vedomost'),
]
