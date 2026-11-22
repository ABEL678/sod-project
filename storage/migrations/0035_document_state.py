"""A1: единое состояние документа вместо status/suitability_code/internal_status.

Шаги:
1. Добавить Document.state и Document.review_result.
2. Перенести данные: CR -> A; состояние по (status, suitability, internal_status)
   и зоне папки; ручные коды S1/S2/S4 -> review_result.
3. Удалить поля status, suitability_code, internal_status.

Обратимость: обратная миграция восстанавливает признаки из состояния
(см. _state_to_legacy). Зона папки не меняется.
"""

from django.db import migrations, models


# Зеркало Document.STATE_PROJECTION (не импортируем модель в миграции).
STATE_PROJECTION = {
    'wip_draft': ('draft', 'S0', 'draft'),
    'wip_internal_review': ('draft', 'S0', 'internal_review'),
    'wip_rework': ('draft', 'S0', 'rework'),
    'wip_ready': ('draft', 'S0', 'ready_to_share'),
    'shared': ('in_review', 'S3', 'draft'),
    'approved': ('approved', 'A', 'draft'),
    'as_built': ('approved', 'A', 'draft'),
    'archived': ('archived', 'A', 'draft'),
}

# Ручные результаты проверок (не признаки состояния) -> review_result.
MANUAL_REVIEW_CODES = ('S1', 'S2', 'S4')


def _legacy_to_state(status, suitability, internal_status, zone):
    """Состояние по прежним признакам; несопоставимое — по статусу/зоне."""
    if status == 'draft' and suitability == 'S0':
        return {
            'draft': 'wip_draft',
            'internal_review': 'wip_internal_review',
            'rework': 'wip_rework',
            'ready_to_share': 'wip_ready',
        }.get(internal_status, 'wip_draft')
    if status == 'in_review':
        return 'shared'
    if status == 'approved':
        return 'approved'
    if status == 'archived':
        return 'archived'
    # Резерв: статус вне таблицы — по зоне папки.
    return {
        'WIP': 'wip_draft',
        'SHARED': 'shared',
        'PUBLISHED': 'approved',
        'ARCHIVE': 'archived',
    }.get(zone, 'wip_draft')


def forwards(apps, schema_editor):
    Document = apps.get_model('storage', 'Document')

    # CR (исполнительная) приравниваем к A по решению владельца.
    Document.objects.filter(suitability_code='CR').update(suitability_code='A')

    for doc in Document.objects.iterator():
        zone = 'WIP'  # зона не влияет на состояние: статус/внутренний статус первичны
        state = _legacy_to_state(
            doc.status, doc.suitability_code, doc.internal_status, zone,
        )
        review = (
            doc.suitability_code
            if doc.suitability_code in MANUAL_REVIEW_CODES else ''
        )
        doc.state = state
        doc.review_result = review
        doc.save(update_fields=['state', 'review_result'])


def backwards(apps, schema_editor):
    Document = apps.get_model('storage', 'Document')
    for doc in Document.objects.iterator():
        st, suit, internal = _state_to_legacy(doc.state)
        if doc.review_result:
            suit = doc.review_result
        doc.status = st
        doc.suitability_code = suit
        doc.internal_status = internal
        doc.save(update_fields=['status', 'suitability_code', 'internal_status'])


def _state_to_legacy(state):
    st, suit, internal = STATE_PROJECTION.get(state, ('draft', 'S0', 'draft'))
    return st, suit, internal


class Migration(migrations.Migration):

    dependencies = [
        ('storage', '0034_s3_sync_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='state',
            field=models.CharField(
                choices=[
                    ('wip_draft', 'В работе'),
                    ('wip_internal_review', 'На внутренней проверке'),
                    ('wip_rework', 'На доработке'),
                    ('wip_ready', 'Готов к передаче'),
                    ('shared', 'На согласовании'),
                    ('approved', 'Утверждён'),
                    ('as_built', 'Исполнительная документация'),
                    ('archived', 'Архив'),
                ],
                default='wip_draft',
                max_length=24,
                verbose_name='Состояние',
            ),
        ),
        migrations.AddField(
            model_name='document',
            name='review_result',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', 'Не задан'),
                    ('S1', 'Для совместной работы'),
                    ('S2', 'Для ознакомления'),
                    ('S4', 'На утверждении'),
                ],
                default='',
                max_length=2,
                verbose_name='Результат последней проверки',
            ),
        ),
        migrations.RunPython(forwards, backwards),
        # Перед удалением задаём дефолты: при откате RemoveField колонки
        # восстанавливаются NOT NULL, а backwards() заполняет значения.
        migrations.AlterField(
            model_name='document',
            name='status',
            field=models.CharField(default='draft', max_length=20),
        ),
        migrations.AlterField(
            model_name='document',
            name='suitability_code',
            field=models.CharField(default='S0', max_length=2),
        ),
        migrations.AlterField(
            model_name='document',
            name='internal_status',
            field=models.CharField(default='draft', max_length=20),
        ),
        migrations.RemoveField(model_name='document', name='status'),
        migrations.RemoveField(model_name='document', name='suitability_code'),
        migrations.RemoveField(model_name='document', name='internal_status'),
    ]
