from django.urls import path

from external_api.views import (
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
]