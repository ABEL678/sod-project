from rest_framework import serializers

from storage.domain.models import Project


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
