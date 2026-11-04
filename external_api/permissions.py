from rest_framework.permissions import BasePermission

from external_api.models import ApiToken


class HasScope(BasePermission):
    """Проверяет, что у токена есть нужный scope."""

    def has_permission(self, request, view):
        required = getattr(view, "required_scope", None)
        if not required:
            return True
        token = getattr(request, "auth", None)
        if not isinstance(token, ApiToken):
            return False
        return required in (token.scopes or [])
