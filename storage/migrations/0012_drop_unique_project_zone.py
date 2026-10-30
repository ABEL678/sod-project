from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('storage', '0010_folder_zone'),
    ]

    operations = [
        migrations.RunSQL(
            sql='DROP INDEX IF EXISTS unique_project_zone;',
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
