from django.contrib import admin
from .domain.models import (
    Company, Project, Section, Document, DocumentVersion, UserProfile, ProjectMembership,
    DocumentView, Specification, VorItem,
    DesignTask, DesignTaskStatusLog, DesignTaskAttachment, TaskTemplate,
    DesignTaskChecklistItem,
)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)
    exclude = ('created_at',)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'company', 'customer', 'status', 'gip')
    list_filter = ('company', 'status')
    search_fields = ('code', 'name')


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'project', 'order')
    list_filter = ('project',)
    search_fields = ('code', 'name')


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'project', 'section', 'doc_type', 'status')
    list_filter = ('project', 'section', 'doc_type', 'status')
    search_fields = ('code', 'name')
    readonly_fields = ('status', 'suitability_code')


@admin.register(DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = ('document', 'version_number', 'uploaded_at', 'uploaded_by')
    list_filter = ('document__project',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'company')
    list_filter = ('role', 'company')
    filter_horizontal = ('allowed_sections',)


@admin.register(ProjectMembership)
class ProjectMembershipAdmin(admin.ModelAdmin):
    list_display = ('project', 'user', 'role_in_project')
    list_filter = ('project', 'role_in_project')


@admin.register(DocumentView)
class DocumentViewAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'user', 'is_shared', 'created_at')
    list_filter = ('project', 'is_shared')
    search_fields = ('name',)


@admin.register(Specification)
class SpecificationAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'name', 'section_code', 'unit', 'quantity', 'project',
    )
    list_filter = ('project', 'section_code')
    search_fields = ('code', 'name')


@admin.register(VorItem)
class VorItemAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'name', 'section_code', 'unit', 'quantity', 'project',
    )
    list_filter = ('project', 'section_code')
    search_fields = ('code', 'name')


# === Модуль «Задания» (DesignTask), Этап 1 — базовая регистрация ===


@admin.register(DesignTask)
class DesignTaskAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'title', 'project', 'source_section', 'target_section',
        'author', 'assignee', 'status', 'priority', 'due_date', 'created_at',
    )
    list_filter = ('project', 'status', 'priority')
    search_fields = ('title', 'description', 'external_id')
    list_select_related = (
        'project', 'source_section', 'target_section', 'author',
        'assignee', 'template',
    )


@admin.register(DesignTaskStatusLog)
class DesignTaskStatusLogAdmin(admin.ModelAdmin):
    list_display = ('task', 'old_status', 'new_status', 'changed_by', 'created_at')
    list_filter = ('new_status',)
    search_fields = ('task__title', 'comment')
    list_select_related = ('task', 'changed_by')


@admin.register(DesignTaskAttachment)
class DesignTaskAttachmentAdmin(admin.ModelAdmin):
    list_display = ('task', 'kind', 'document', 'file', 'uploaded_by', 'created_at')
    list_filter = ('kind',)
    search_fields = ('task__title',)
    list_select_related = ('task', 'document', 'uploaded_by')


@admin.register(TaskTemplate)
class TaskTemplateAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'company', 'source_section', 'target_section',
        'default_duration_days', 'is_active',
    )
    list_filter = ('company', 'is_active')
    search_fields = ('title', 'description')


@admin.register(DesignTaskChecklistItem)
class DesignTaskChecklistItemAdmin(admin.ModelAdmin):
    list_display = ('task', 'text', 'is_done', 'done_by', 'done_at')
    list_filter = ('is_done',)
    search_fields = ('text', 'task__title')
    list_select_related = ('task', 'done_by')
