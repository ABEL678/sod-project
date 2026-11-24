from django.contrib import admin

from .models import Issue, IssueFlagLog


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    """Замечания в админке: блокирующий флаг — только чтение.

    Смена флага выполняется в интерфейсе замечания через сервис
    (автор или ГИП проекта, с обоснованием и записью в журнал).
    """

    list_display = (
        'id', 'document', 'status', 'is_blocking', 'author', 'created_at',
    )
    list_filter = ('status', 'is_blocking')
    search_fields = ('description', 'document__code', 'document__name')
    readonly_fields = (
        'is_blocking', 'created_at', 'updated_at', 'closed_at', 'closed_by',
    )


@admin.register(IssueFlagLog)
class IssueFlagLogAdmin(admin.ModelAdmin):
    list_display = ('issue', 'old_value', 'new_value', 'changed_by', 'created_at')
    list_filter = ('old_value', 'new_value')
    search_fields = ('issue__description', 'justification')
    readonly_fields = (
        'issue', 'old_value', 'new_value', 'justification', 'changed_by',
        'created_at',
    )

    def has_add_permission(self, request):
        return False
