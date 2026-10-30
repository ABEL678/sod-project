from django.urls import path

from storage.adapters import views

app_name = 'storage'

urlpatterns = [
    path('', views.ProjectListView.as_view(), name='project_list'),
    path('projects/create/', views.ProjectCreateView.as_view(), name='project_create'),
    path('projects/<int:pk>/', views.ProjectDetailView.as_view(), name='project_detail'),
    path('projects/<int:project_id>/sections/create/', views.SectionCreateView.as_view(), name='section_create'),
    path('projects/<int:project_id>/folders/create/', views.FolderCreateView.as_view(), name='folder_create'),
    path('projects/<int:project_id>/folders/create-form/', views.FolderCreateFormView.as_view(), name='folder_create_form'),
    path('documents/<int:document_id>/move/', views.DocumentMoveView.as_view(), name='document_move'),
    path('documents/<int:document_id>/copy/', views.DocumentCopyView.as_view(), name='document_copy'),
    path('documents/<int:document_id>/delete/', views.DocumentDeleteView.as_view(), name='document_delete'),
    path('folders/<int:folder_id>/move/', views.FolderMoveView.as_view(), name='folder_move'),
    path('projects/<int:project_id>/bulk/', views.DocumentBulkActionView.as_view(), name='document_bulk_action'),
    path('projects/<int:project_id>/trash/restore/', views.TrashRestoreView.as_view(), name='trash_restore'),
    path('projects/<int:project_id>/trash/clear/', views.TrashClearView.as_view(), name='trash_clear'),
    path('folders/<int:folder_id>/rename/', views.FolderRenameView.as_view(), name='folder_rename'),
    path('folders/<int:folder_id>/rename-form/', views.FolderRenameFormView.as_view(), name='folder_rename_form'),
    path('folders/<int:folder_id>/delete/', views.FolderDeleteView.as_view(), name='folder_delete'),
    path('folders/<int:folder_id>/download/', views.FolderDownloadView.as_view(), name='folder_download'),
    path('documents/<int:document_id>/share/', views.DocumentShareLinkView.as_view(), name='document_share_link'),
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
