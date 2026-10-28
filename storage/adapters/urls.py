from django.urls import path

from storage.adapters import views

app_name = 'storage'

urlpatterns = [
    path('', views.ProjectListView.as_view(), name='project_list'),
    path('projects/<int:pk>/', views.ProjectDetailView.as_view(), name='project_detail'),
    path('projects/<int:project_id>/search/', views.SearchView.as_view(), name='search'),
    path('projects/<int:project_id>/upload-ajax/', views.DocumentUploadAjaxView.as_view(), name='document_upload_ajax'),
    path('projects/<int:project_id>/views/save/', views.DocumentViewSaveView.as_view(), name='document_view_save'),
    path(
        'projects/<int:project_id>/memberships/<int:membership_id>/role/',
        views.MembershipRoleUpdateView.as_view(),
        name='membership_role_update',
    ),
    path('documents/<int:pk>/', views.DocumentDetailView.as_view(), name='document_detail'),
    path('download/<int:version_id>/', views.DocumentDownloadView.as_view(), name='document_download'),
    path('projects/<int:project_id>/export/', views.ExportVedomostView.as_view(), name='export_vedomost'),
    path('projects/<int:project_id>/mail-import/', views.SimulateMailImportView.as_view(), name='simulate_mail_import'),
]
