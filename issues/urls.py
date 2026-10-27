from django.urls import path

from issues import views

app_name = 'issues'

urlpatterns = [
    path('documents/<int:document_id>/issues/create/', views.IssueCreateView.as_view(), name='issue_create'),
    path('issues/<int:issue_id>/status/', views.IssueUpdateStatusView.as_view(), name='issue_status'),
]
