from django.urls import path

from issues import views

app_name = 'issues'

urlpatterns = [
    path('projects/<int:project_id>/issues/', views.IssueRegistryView.as_view(), name='issue_registry'),
    path('projects/<int:project_id>/issues/export/', views.IssueRegistryExportView.as_view(), name='issue_registry_export'),
    path('my-issues/', views.MyIssuesView.as_view(), name='my_issues'),
    path('notifications/', views.notifications_partial, name='notifications_partial'),
    path('notifications/read-all/', views.notifications_read_all, name='notifications_read_all'),
    path('documents/<int:document_id>/issues/create/', views.IssueCreateView.as_view(), name='issue_create'),
    path('issues/<int:issue_id>/status/', views.IssueUpdateStatusView.as_view(), name='issue_status'),
    path('issues/<int:issue_id>/answer/', views.IssueAnswerUpdateView.as_view(), name='issue_answer'),
    path('issues/<int:issue_id>/delete/', views.IssueDeleteView.as_view(), name='issue_delete'),
    path('issues/<int:issue_id>/delete-crop/', views.issue_delete_crop, name='issue_delete_crop'),
    path('issues/<int:issue_id>/detail/', views.issue_detail_htmx, name='issue_detail_htmx'),
    path('issues/<int:issue_id>/comment/', views.add_comment, name='add_comment'),
]
