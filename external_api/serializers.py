from decimal import Decimal

from rest_framework import serializers

from issues.models import Issue
from storage.domain.models import Project, Specification


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
