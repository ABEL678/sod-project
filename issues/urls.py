from django.urls import path

from issues import views

app_name = 'issues'

urlpatterns = [
    path('documents/<int:document_id>/issues/create/', views.IssueCreateView.as_view(), name='issue_create'),
    path('issues/<int:issue_id>/status/', views.IssueUpdateStatusView.as_view(), name='issue_status'),
    path('issues/<int:issue_id>/answer/', views.IssueAnswerUpdateView.as_view(), name='issue_answer'),
    path('issues/<int:issue_id>/delete/', views.IssueDeleteView.as_view(), name='issue_delete'),
    path('issues/<int:issue_id>/delete-crop/', views.issue_delete_crop, name='issue_delete_crop'),
    path('issues/<int:issue_id>/detail/', views.issue_detail_htmx, name='issue_detail_htmx'),
]
