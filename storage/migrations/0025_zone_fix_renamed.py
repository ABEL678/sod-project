from django.db import migrations


def mark_renamed_non_wip_zones(apps, schema_editor):
    """Fallback для БД, где 0024 уже применена.

    Корневые папки с заполненной не-WIP зоной считаем системными.
    Переименованную WIP-зону по данным отличить нельзя — это крайний
    случай, требующий ручной пометки is_system_zone.
    """
    Folder = apps.get_model('storage', 'Folder')
    Folder.objects.filter(
        parent__isnull=True,
        section__isnull=True,
    ).exclude(zone='WIP').update(is_system_zone=True)


class Migration(migrations.Migration):

    dependencies = [
        ('storage', '0024_zone_identity_field'),
    ]

    operations = [
        migrations.RunPython(
            mark_renamed_non_wip_zones,
            migrations.RunPython.noop,
        ),
    ]
