from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from issues.models import Issue
from storage.domain.models import DesignTask, Project, RfiTask, Specification


# Маппинг статусов DesignTask → ga_status (раздел 10.4 плана RFI).
GA_STATUS_MAP = {
    DesignTask.Status.DRAFT: 'NOT_STARTED',
    DesignTask.Status.ISSUED: 'NOT_STARTED',
    DesignTask.Status.IN_PROGRESS: 'IN_PROGRESS',
    DesignTask.Status.ON_HOLD: 'REWORK',
    DesignTask.Status.COMPLETED: 'REVIEW',
    DesignTask.Status.REVIEW: 'REVIEW',
    DesignTask.Status.ACCEPTED: 'DONE',
    DesignTask.Status.RETURNED: 'REWORK',
    DesignTask.Status.CANCELLED: 'CANCELLED',
}


def _user_data(user):
    if user is None:
        return None
    return {'username': user.username, 'full_name': user.get_full_name() or user.username}


class ApiTaskReadSerializer(serializers.Serializer):
    """Дизайн-задание для внешнего API (формат раздела 10.4)."""

    id = serializers.IntegerField()
    external_id = serializers.CharField(allow_null=True)
    title = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField(source='get_status_display')
    ga_status = serializers.SerializerMethodField()
    priority = serializers.CharField()
    priority_label = serializers.CharField(source='get_priority_display')
    source_section = serializers.SerializerMethodField()
    target_section = serializers.SerializerMethodField()
    author = serializers.SerializerMethodField()
    assignee = serializers.SerializerMethodField()
    due_date = serializers.DateField(allow_null=True)
    is_overdue = serializers.SerializerMethodField()
    overdue_days = serializers.SerializerMethodField()
    days_until_deadline = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    url = serializers.SerializerMethodField()

    def get_ga_status(self, task):
        return GA_STATUS_MAP.get(task.status, 'NOT_STARTED')

    def get_source_section(self, task):
        s = task.source_section
        return {'code': s.code, 'name': s.name} if s else None

    def get_target_section(self, task):
        s = task.target_section
        return {'code': s.code, 'name': s.name} if s else None

    def get_author(self, task):
        return _user_data(task.author)

    def get_assignee(self, task):
        return _user_data(task.assignee)

    def get_is_overdue(self, task):
        today = timezone.localdate()
        return bool(task.due_date and task.due_date < today)

    def get_overdue_days(self, task):
        today = timezone.localdate()
        if task.due_date and task.due_date < today:
            return (today - task.due_date).days
        return 0

    def get_days_until_deadline(self, task):
        today = timezone.localdate()
        if task.due_date:
            delta = (task.due_date - today).days
            return max(delta, 0)
        return None

    def get_url(self, task):
        from django.urls import reverse
        return reverse('storage:task_detail', args=[task.project_id, task.pk])


class ApiRfiReadSerializer(serializers.Serializer):
    """RFI для внешнего API (формат раздела 10.5)."""

    id = serializers.IntegerField()
    number = serializers.IntegerField()
    number_display = serializers.SerializerMethodField()
    external_id = serializers.CharField(allow_null=True)
    title = serializers.CharField(source='subject')
    description = serializers.CharField(source='reason')
    status = serializers.CharField()
    status_label = serializers.CharField(source='get_status_display')
    ga_rfi_state = serializers.CharField(source='status')
    author = serializers.SerializerMethodField()
    assignee = serializers.SerializerMethodField()
    rfi_due_date = serializers.DateField(allow_null=True)
    is_overdue = serializers.SerializerMethodField()
    overdue_days = serializers.SerializerMethodField()
    sent_at = serializers.DateTimeField(allow_null=True)
    answered_at = serializers.DateTimeField(allow_null=True)
    registered_at = serializers.DateTimeField(allow_null=True)
    escalated_at = serializers.DateTimeField(allow_null=True)
    closed_at = serializers.DateTimeField(allow_null=True)
    request_document = serializers.SerializerMethodField()
    response_document = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()

    def get_number_display(self, rfi):
        return f'RFI-{rfi.number:02d}'

    def get_author(self, rfi):
        return _user_data(rfi.author)

    def get_assignee(self, rfi):
        return _user_data(rfi.assignee)

    def get_is_overdue(self, rfi):
        today = timezone.localdate()
        return bool(rfi.rfi_due_date and rfi.rfi_due_date < today)

    def get_overdue_days(self, rfi):
        today = timezone.localdate()
        if rfi.rfi_due_date and rfi.rfi_due_date < today:
            return (today - rfi.rfi_due_date).days
        return 0

    def get_request_document(self, rfi):
        if rfi.document_request:
            return {'code': rfi.document_request.code, 'name': rfi.document_request.name}
        return None

    def get_response_document(self, rfi):
        if rfi.document_response:
            return {'code': rfi.document_response.code, 'name': rfi.document_response.name}
        return None

    def get_url(self, rfi):
        from django.urls import reverse
        return reverse('storage:rfi_detail', args=[rfi.project_id, rfi.pk])


class ProjectCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=255)
    customer = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        default="",
    )
    base_code = serializers.CharField(
        max_length=50,
        required=False,
        allow_blank=True,
        default="",
    )
    template_key = serializers.ChoiceField(
        choices=["PD_87", "RD_GOST", "FULL"],
        required=False,
        default="FULL",
    )

    def validate_code(self, value):
        if Project.objects.filter(code=value).exists():
            raise serializers.ValidationError(
                "Проект с таким кодом уже существует."
            )
        return value


class SpecificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Specification
        fields = [
            "id",
            "code",
            "name",
            "section_code",
            "unit",
            "quantity",
            "note",
        ]


class VorItemSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=100)
    name = serializers.CharField(max_length=255)
    unit = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )
    quantity = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    section_code = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )


class IssueSyncSerializer(serializers.Serializer):
    external_id = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
    document_code = serializers.CharField(max_length=100)
    issue_text = serializers.CharField()
    status = serializers.ChoiceField(
        choices=[code for code, _ in Issue.STATUS_CHOICES],
        required=False,
        default="NEW",
    )
    page_number = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )
    x_ratio = serializers.FloatField(
        required=False, allow_null=True, min_value=0, max_value=1
    )
    y_ratio = serializers.FloatField(
        required=False, allow_null=True, min_value=0, max_value=1
    )

    def validate(self, attrs):
        has_page = attrs.get("page_number") is not None
        has_x = attrs.get("x_ratio") is not None
        has_y = attrs.get("y_ratio") is not None
        if any((has_page, has_x, has_y)) and not all(
            (has_page, has_x, has_y)
        ):
            raise serializers.ValidationError(
                "page_number, x_ratio и y_ratio задаются вместе"
            )
        return attrs


class IssuePendingSerializer(serializers.ModelSerializer):
    document_code = serializers.CharField(source="document.code")
    text = serializers.CharField(source="description")
    external_id = serializers.CharField(allow_null=True)
    assignee_username = serializers.SerializerMethodField()

    class Meta:
        model = Issue
        fields = [
            "id",
            "external_id",
            "document_code",
            "text",
            "status",
            "created_at",
            "assignee_username",
        ]

    def get_assignee_username(self, issue):
        return issue.assignee.username if issue.assignee else None
