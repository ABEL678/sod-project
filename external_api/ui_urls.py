from django.urls import path

from external_api import views

app_name = "external_api"

urlpatterns = [
    path("api-tokens/", views.ApiTokenListView.as_view(), name="api_token_list"),
    path(
        "api-tokens/create/",
        views.ApiTokenCreateView.as_view(),
        name="api_token_create",
    ),
    path(
        "api-tokens/<int:token_id>/revoke/",
        views.ApiTokenRevokeView.as_view(),
        name="api_token_revoke",
    ),
]
