"""Зачистка 2026-11-09: удаление моделей ИИ-нормоконтроля СПДС.

GigaCode удалил UI нормоконтроля (шаблоны, вьюхи, URL, FAB) — этот
коммит завершает зачистку: убираются модели и их таблицы:
- RuleDefinition — правила проверок (движок ai_checks/domain/rules.py);
- CheckRun — прогоны проверки версий;
- PendingCheckResult — находки прогона;
- ReminderRule — напоминания по незавершённым находкам (Ф4).

Связанные поля/настройки тоже сняты:
- LLMCallLog.check_run (FK на удаляемый CheckRun) и
  LLMCallLog.purpose='check_explain' (назначение было только у находок);
- CELERY_BEAT_SCHEDULE['ai-checks-reminders-daily'] в settings.py.

Остаются рабочими: RAG-поиск (RagQuery, ProjectDocumentChunk),
база знаний (InternalDocument/InternalChunk), отчёт ГИПу
(GeneratedReport) и журнал вызовов LLM (LLMCallLog).

Порядок операций важен: сначала снимается FK LLMCallLog.check_run,
затем удаляется PendingCheckResult (FK на CheckRun и RuleDefinition),
потом RuleDefinition, CheckRun и ReminderRule. Данные не сохраняются —
таблицы нормоконтроля опустошаются вместе с содержимым.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai_checks', '0012_seed_discipline_standards'),
    ]

    operations = [
        # FK на удаляемый CheckRun снимаем первой.
        migrations.RemoveField(
            model_name='llmcalllog',
            name='check_run',
        ),
        migrations.AlterField(
            model_name='llmcalllog',
            name='purpose',
            field=models.CharField(
                choices=[
                    ('rag_answer', 'Ответ RAG'),
                    ('gip_report', 'Отчёт ГИПа'),
                ],
                max_length=30,
                verbose_name='Назначение',
            ),
        ),
        # Находки ссылаются на CheckRun и RuleDefinition — раньше их.
        migrations.DeleteModel(
            name='PendingCheckResult',
        ),
        migrations.DeleteModel(
            name='RuleDefinition',
        ),
        migrations.DeleteModel(
            name='CheckRun',
        ),
        migrations.DeleteModel(
            name='ReminderRule',
        ),
    ]
