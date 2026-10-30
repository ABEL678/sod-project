from django.apps import AppConfig
from django.apps import apps as django_apps
from django.db.models.signals import post_migrate


class StorageConfig(AppConfig):
    name = 'storage'


def _handle_post_migrate(sender, **kwargs):
    """Создаёт зоны для проектов, у которых не все 4 канонические зоны."""
    if sender.name != 'storage':
        return
    try:
        Folder = django_apps.get_model('storage', 'Folder')
        Project = django_apps.get_model('storage', 'Project')
        User = django_apps.get_model('auth', 'User')
    except LookupError:
        return

    required_zones = {code for code, _ in Folder.ZONE_CHOICES}
    canonical_names = {'01. В работе', '02. На согласовании', '03. Опубликовано', '04. Архив'}

    projects = Project.objects.all()
    for project in projects:
        existing = set(
            Folder.objects.filter(
                project=project,
                zone__in=dict(Folder.ZONE_CHOICES),
                parent__isnull=True,
                section__isnull=True,
            ).values_list('zone', flat=True)
        )
        missing_zones = required_zones - existing
        if not missing_zones:
            continue

        creator = project.created_by if project.created_by_id else None
        if not creator:
            continue

        zone_name_map = {
            'WIP': '01. В работе',
            'SHARED': '02. На согласовании',
            'PUBLISHED': '03. Опубликовано',
            'ARCHIVE': '04. Архив',
        }
        zone_order_map = {
            'WIP': 1,
            'SHARED': 2,
            'PUBLISHED': 3,
            'ARCHIVE': 4,
        }
        for zone in missing_zones:
            Folder.objects.create(
                project=project,
                section=None,
                parent=None,
                name=zone_name_map[zone],
                order=zone_order_map[zone],
                zone=zone,
                created_by=creator,
            )


post_migrate.connect(_handle_post_migrate)
