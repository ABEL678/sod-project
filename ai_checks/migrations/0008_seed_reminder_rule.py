"""Ф4: сид правила напоминаний (idempotent).

Расписание ежедневного запуска check_reminders — статическое, в
sod_core/settings.py (CELERY_BEAT_SCHEDULE, cron: ежедневно 08:00).
django-celery-beat НЕ используется: пакет 2.9.0 требует Django<6.1,
а проект pinned на Django==6.1.2 (решение аудита Ф4 от 07.11.2026).
"""

from django.db import migrations

REMINDER_CODE = 'CHECK_PENDING_7'


def seed_reminder_rule(apps, schema_editor):
    ReminderRule = apps.get_model('ai_checks', 'ReminderRule')
    ReminderRule.objects.update_or_create(
        code=REMINDER_CODE,
        defaults={
            'offset_days': 7,
            'audience': 'gip',
            'is_active': True,
        },
    )


def unseed_reminder_rule(apps, schema_editor):
    ReminderRule = apps.get_model('ai_checks', 'ReminderRule')
    ReminderRule.objects.filter(code=REMINDER_CODE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('ai_checks', '0007_reminderrule_generatedreport'),
    ]

    operations = [
        migrations.RunPython(seed_reminder_rule, unseed_reminder_rule),
    ]
