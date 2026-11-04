from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views import View
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from external_api.authentication import ApiTokenAuthentication
from external_api.models import ApiToken
from external_api.permissions import HasScope
from external_api.serializers import ProjectCreateSerializer
from storage.domain.models import Company
from storage.domain.services import (
    create_api_token,
    create_project_from_template,
    get_user_company,
    list_api_tokens,
    revoke_api_token,
)


class ProjectCreateView(APIView):
    """POST /api/v1/projects/ — создание проекта внешней системой."""

    authentication_classes = [ApiTokenAuthentication]
    permission_classes = [IsAuthenticated, HasScope]
    required_scope = ApiToken.Scope.PROJECTS_CREATE

    def post(self, request):
        serializer = ProjectCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        token = request.auth

        try:
            project = create_project_from_template(
                company=token.company,
                code=data["code"],
                name=data["name"],
                customer=data.get("customer", ""),
                gip=request.user,
                created_by=request.user,
                template_key=data.get("template_key", "FULL"),
                base_code=data.get("base_code", ""),
            )
        except DjangoValidationError as exc:
            errors = (
                exc.message_dict
                if getattr(exc, "error_dict", None)
                else {"non_field_errors": exc.messages}
            )
            return Response({"errors": errors}, status=400)
        except ValueError as exc:
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=400,
            )
        except PermissionDenied as exc:
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=403,
            )

        return Response(
            {"id": project.id, "code": project.code, "name": project.name},
            status=201,
        )


class ApiTokenAdminMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Доступ к управлению токенами — только admin компании / superuser."""

    raise_exception = True

    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True
        profile = getattr(user, "userprofile", None)
        return profile is not None and profile.role == "admin"

    def resolve_company(self):
        """Возвращает (company, companies) с учётом выбора суперюзера."""
        user = self.request.user
        if not user.is_superuser:
            return get_user_company(user), None

        companies = Company.objects.order_by("name")
        raw_company = (
            self.request.GET.get("company")
            or self.request.POST.get("company")
            or ""
        ).strip()
        company = None
        if raw_company:
            try:
                company = companies.filter(pk=int(raw_company)).first()
            except (TypeError, ValueError):
                company = None
        if company is None:
            company = get_user_company(user) or companies.first()
        return company, companies


class ApiTokenListView(ApiTokenAdminMixin, View):
    """Страница управления API-токенами компании."""

    def get(self, request):
        company, companies = self.resolve_company()
        if company is None:
            return HttpResponseBadRequest("Не указана компания")

        tokens = list_api_tokens(request.user, company)
        scope_labels = dict(ApiToken.Scope.choices)
        now = timezone.now()
        for token in tokens:
            token.scope_labels = [
                scope_labels.get(scope, scope) for scope in (token.scopes or [])
            ]
            token.is_expired = bool(
                token.expires_at and token.expires_at < now
            )
        created_token = request.session.pop("api_token_raw_once", None)

        return render(
            request,
            "external_api/api_tokens.html",
            {
                "company": company,
                "companies": companies,
                "selected_company_id": company.pk,
                "tokens": tokens,
                "scope_choices": ApiToken.Scope.choices,
                "created_token": created_token,
            },
        )


class ApiTokenCreateView(ApiTokenAdminMixin, View):
    """Создаёт токен и показывает raw-значение один раз."""

    def post(self, request):
        company, _companies = self.resolve_company()
        if company is None:
            return HttpResponseBadRequest("Не указана компания")

        try:
            expires_at = _parse_expires_at(request.POST.get("expires_at"))
            token, raw_token = create_api_token(
                request.user,
                company,
                request.POST.get("name", ""),
                request.POST.getlist("scopes"),
                expires_at=expires_at,
            )
        except DjangoValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(_tokens_url(request.user, company))
        except PermissionDenied:
            return HttpResponseForbidden("Нет доступа к API-токенам")

        request.session["api_token_raw_once"] = {
            "name": token.name,
            "token": raw_token,
        }
        messages.success(request, "Токен создан. Скопируйте его сейчас.")
        return redirect(_tokens_url(request.user, company))


class ApiTokenRevokeView(ApiTokenAdminMixin, View):
    """Отзывает API-токен компании."""

    def post(self, request, token_id):
        token = get_object_or_404(ApiToken, pk=token_id)
        try:
            revoke_api_token(request.user, token)
        except DjangoValidationError:
            return HttpResponseBadRequest("Неверный токен")
        except PermissionDenied:
            return HttpResponseForbidden("Нет доступа к API-токенам")

        messages.success(request, f"Токен «{token.name}» отозван.")
        return redirect(_tokens_url(request.user, token.company))


def _parse_expires_at(raw_value):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None

    value = parse_datetime(raw_value)
    if value is None:
        date_value = parse_date(raw_value)
        if date_value is None:
            raise DjangoValidationError(
                {"expires_at": "Неверная дата окончания действия"}
            )
        value = datetime.combine(date_value, time.min)

    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value


def _tokens_url(user, company):
    url = reverse("external_api:api_token_list")
    if user.is_superuser:
        return f"{url}?company={company.pk}"
    return url
