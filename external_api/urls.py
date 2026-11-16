from django.urls import path

from external_api.views import (
    ApiRfiListView,
    ApiTaskListView,
    IssuePendingView,
    IssueSyncView,
    ProjectCreateView,
    SpecificationListView,
    VorCreateView,
)

urlpatterns = [
    path(
        "projects/",
        ProjectCreateView.as_view(),
        name="api_project_create",
    ),
    path(
        "projects/<str:code>/specifications/",
        SpecificationListView.as_view(),
        name="api_project_specifications",
    ),
    path(
        "projects/<str:code>/vor/",
        VorCreateView.as_view(),
        name="api_project_vor",
    ),
    path(
        "projects/<str:code>/issues/sync/",
        IssueSyncView.as_view(),
        name="api_project_issue_sync",
    ),
    path(
        "projects/<str:code>/issues/pending/",
        IssuePendingView.as_view(),
        name="api_project_issue_pending",
    ),
    path(
        "projects/<str:code>/tasks/",
        ApiTaskListView.as_view(),
        name="api_project_tasks",
    ),
    path(
        "projects/<str:code>/rfis/",
        ApiRfiListView.as_view(),
        name="api_project_rfis",
    ),
]
