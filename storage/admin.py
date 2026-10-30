from django.contrib import admin
from .domain.models import (
    Company, Project, Section, Document, DocumentVersion, UserProfile, ProjectMembership,
    DocumentView,
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
