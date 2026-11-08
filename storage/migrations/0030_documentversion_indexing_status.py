# Треб. 3: статус ИИ-индексации версии документа проекта
# (pending/processing/done/error). Значения согласованы с
# ai_checks.domain.models.IndexingStatus; choices дублируются без
# импорта из ai_checks — домен storage не зависит от ИИ-контура.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('storage', '0029_vor_item'),
    ]

    operations = [
        migrations.AddField(
            model_name='documentversion',
            name='indexing_status',
            field=models.CharField(
                choices=[
                    ('pending', 'В очереди'),
                    ('processing', 'Индексируется'),
                    ('done', 'Готово'),
                    ('error', 'Ошибка'),
                ],
                default='pending',
                max_length=10,
                verbose_name='Статус ИИ-индексации',
            ),
        ),
    ]
