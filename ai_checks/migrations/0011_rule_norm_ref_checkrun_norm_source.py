"""Блоки 2–3 бэта-фиксов 2026-11-08: norm_ref правил и норматив/лист прогона.

- RuleDefinition.norm_ref — полное нормативное обоснование
  («ГОСТ 21.613-2014, п. 5.2») для панели проверки;
- CheckRun.page_number — «Проверить лист» (проверка одного листа);
- CheckRun.norm_source / norm_source_label — селектор «Проверка по»
  (базовый ГОСТ Р 21.101-2026 или внутренний стандарт из БЗ).
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ai_checks', '0010_projectdocumentchunk_ragquery_indexing_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='ruledefinition',
            name='norm_ref',
            field=models.CharField(
                blank=True,
                default='',
                max_length=200,
                verbose_name='Нормативное обоснование',
            ),
        ),
        migrations.AddField(
            model_name='checkrun',
            name='page_number',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name='Проверенный лист',
            ),
        ),
        migrations.AddField(
            model_name='checkrun',
            name='norm_source',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='check_runs',
                to='ai_checks.internaldocument',
                verbose_name='Нормативный источник проверки',
            ),
        ),
        migrations.AddField(
            model_name='checkrun',
            name='norm_source_label',
            field=models.CharField(
                blank=True,
                default='',
                max_length=255,
                verbose_name='Проверка по (норматив) — снимок названия',
            ),
        ),
    ]
