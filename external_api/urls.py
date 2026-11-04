from django.urls import path

from external_api.views import ProjectCreateView

urlpatterns = [
    path(
        "projects/",
        ProjectCreateView.as_view(),
        name="api_project_create",
    ),
]
