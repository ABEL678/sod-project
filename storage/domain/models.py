from django.db import models
from django.contrib.auth.models import User


class Project(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    customer = models.CharField(max_length=255, blank=True)
    gip = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='gip_projects')
    STATUS_CHOICES = [
        ('active', 'Активен'),
        ('archived', 'Архив'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_projects')

    def __str__(self):
        return f'{self.code} - {self.name}'


class Section(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='sections')
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=255)
    order = models.IntegerField()
    folder_template = models.CharField(max_length=100, blank=True)

    class Meta:
        unique_together = ('project', 'code')

    def __str__(self):
        return f'{self.code} - {self.name}'


class Document(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='documents')
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='documents')
    code = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    DOC_TYPE_CHOICES = [
        ('drawing', 'Чертёж'),
        ('source_data', 'Исходные данные'),
        ('report', 'Отчет'),
        ('other', 'Другое'),
    ]
    doc_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES)
    current_version = models.ForeignKey(
        'DocumentVersion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    STATUS_CHOICES = [
        ('draft', 'Черновик'),
        ('in_review', 'На проверке'),
        ('approved', 'Утвержден'),
        ('archived', 'Архив'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('project', 'section', 'code')

    def __str__(self):
        return f'{self.code} - {self.name}'


class DocumentVersion(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='versions')
    version_number = models.IntegerField()
    file = models.FileField(upload_to='documents/%Y/%m/')
    file_size = models.BigIntegerField()
    file_hash = models.CharField(max_length=64, blank=True)
    comment = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT)

    class Meta:
        unique_together = ('document', 'version_number')

    def __str__(self):
        return f'v{self.version_number} - {self.document.code}'


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    ROLE_CHOICES = [
        ('admin', 'Админ'),
        ('gip', 'ГИП'),
        ('engineer', 'Инженер'),
        ('viewer', 'Наблюдатель'),
        ('contractor', 'Контрактор'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    allowed_sections = models.ManyToManyField(Section, blank=True)

    def __str__(self):
        return f'{self.user.username} - {self.role}'


class ProjectMembership(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    ROLE_IN_PROJECT_CHOICES = [
        ('gip', 'ГИП'),
        ('engineer', 'Инженер'),
        ('viewer', 'Наблюдатель'),
        ('contractor', 'Контрактор'),
    ]
    role_in_project = models.CharField(max_length=20, choices=ROLE_IN_PROJECT_CHOICES)

    class Meta:
        unique_together = ('project', 'user')

    def __str__(self):
        return f'{self.user.username} - {self.project.code} ({self.role_in_project})'