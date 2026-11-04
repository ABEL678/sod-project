import hashlib

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from external_api.models import ApiToken


class ApiTokenAuthentication(BaseAuthentication):
    """Аутентификация по заголовку Authorization: Bearer <token>."""

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != b"bearer":
            return None
        if len(auth) != 2:
            raise AuthenticationFailed("Invalid token header.")

        try:
            raw_token = auth[1].decode()
        except UnicodeDecodeError:
            raise AuthenticationFailed("Invalid token header.")

        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        try:
            token = ApiToken.objects.select_related("company", "created_by").get(
                token_hash=token_hash,
                is_active=True,
            )
        except ApiToken.DoesNotExist:
            raise AuthenticationFailed("Invalid or inactive token.")

        if token.expires_at and token.expires_at < timezone.now():
            raise AuthenticationFailed("Token expired.")

        if token.created_by is None or not token.created_by.is_active:
            raise AuthenticationFailed("Token has no active owner.")

        ApiToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())

        return (token.created_by, token)

    def authenticate_header(self, request):
        return "Bearer"
