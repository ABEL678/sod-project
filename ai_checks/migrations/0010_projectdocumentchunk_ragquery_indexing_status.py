"""Финальная архитектура ИИ-контура (треб. 1-3).

- ProjectDocumentChunk: чанки PDF-версий документов проекта с вектором
  bge-m3 (1024) — проектный индекс сквозного RAG (треб. 1). Уникальность
  (document, version, seq); HNSW/cosine-индекс — как у InternalChunk
  (только PostgreSQL, на SQLite no-op).
- RagQuery: история ИИ-поиска (треб. 2), индекс (project, -created_at).
- InternalDocument.index_status → indexing_status (треб. 3): переименование
  с конвертацией значений queued/running/done/failed →
  pending/processing/done/error (единый IndexingStatus).

HNSW-индекс повторяет паттерн 0003_hnsw_index (расширение `vector`
создаётся там же раньше по порядку зависимостей).
"""

from django.db import migrations, models
import django.db.models.deletion
from pgvector.django import VectorField

PROJECT_CHUNK_HNSW_NAME = 'ai_project_chunk_embedding_hnsw'

# Треб. 3: конвертация старых значений статуса в новые.
STATUS_FORWARD = {
    'queued': 'pending',
    'running': 'processing',
    'done': 'done',
    'failed': 'error',
}
STATUS_BACKWARD = {
    'pending': 'queued',
    'processing': 'running',
    'done': 'done',
    'error': 'failed',
}


def convert_status_forward(apps, schema_editor):
    InternalDocument = apps.get_model('ai_checks', 'InternalDocument')
    for old, new in STATUS_FORWARD.items():
        InternalDocument.objects.filter(indexing_status=old).update(
            indexing_status=new,
        )


def convert_status_backward(apps, schema_editor):
    InternalDocument = apps.get_model('ai_checks', 'InternalDocument')
    for new, old in STATUS_BACKWARD.items():
        InternalDocument.objects.filter(indexing_status=new).update(
            indexing_status=old,
        )


def create_project_chunk_hnsw(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        f'CREATE INDEX IF NOT EXISTS {PROJECT_CHUNK_HNSW_NAME} '
        'ON ai_checks_projectdocumentchunk '
        'USING hnsw (embedding vector_cosine_ops) '
        'WITH (m=16, ef_construction=64);'
    )


def drop_project_chunk_hnsw(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(f'DROP INDEX IF EXISTS {PROJECT_CHUNK_HNSW_NAME};')


class Migration(migrations.Migration):

    dependencies = [
        ('ai_checks', '0009_internaldocument_index_error_and_more'),
    ]

    operations = [
        # Треб. 3 (InternalDocument): переименование и конвертация значений.
        migrations.RenameField(
            model_name='internaldocument',
            old_name='index_status',
            new_name='indexing_status',
        ),
        migrations.RunPython(convert_status_forward, convert_status_backward),
        migrations.AlterField(
            model_name='internaldocument',
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
                verbose_name='Статус индексации',
            ),
        ),
        # Треб. 1: чанки PDF-версий документов проекта.
        migrations.CreateModel(
            name='ProjectDocumentChunk',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('seq', models.PositiveIntegerField(verbose_name='Порядок фрагмента')),
                ('text', models.TextField(verbose_name='Текст фрагмента')),
                ('page_from', models.PositiveIntegerField(blank=True, null=True, verbose_name='Страница с')),
                ('page_to', models.PositiveIntegerField(blank=True, null=True, verbose_name='Страница по')),
                ('embedding', VectorField(dimensions=1024, verbose_name='Вектор (bge-m3 dense)')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_chunks', to='storage.document', verbose_name='Документ проекта')),
                ('version', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_chunks', to='storage.documentversion', verbose_name='Версия документа')),
                ('section', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='ai_chunks', to='storage.section', verbose_name='Раздел')),
                ('folder', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='ai_chunks', to='storage.folder', verbose_name='Папка')),
            ],
            options={
                'verbose_name': 'Фрагмент документа проекта',
                'verbose_name_plural': 'Фрагменты документов проектов',
                'ordering': ['document', 'seq'],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('document', 'version', 'seq'),
                        name='ai_project_chunk_doc_ver_seq',
                    ),
                ],
            },
        ),
        # Треб. 2: история ИИ-поиска.
        migrations.CreateModel(
            name='RagQuery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question', models.TextField(verbose_name='Вопрос')),
                ('answer', models.TextField(blank=True, verbose_name='Ответ')),
                ('sources', models.JSONField(blank=True, default=list, verbose_name='Источники')),
                ('model', models.CharField(blank=True, max_length=100, verbose_name='Модель LLM')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Создан')),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='rag_queries', to='storage.project', verbose_name='Проект')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rag_queries', to='auth.user', verbose_name='Автор')),
            ],
            options={
                'verbose_name': 'Запрос ИИ-поиска',
                'verbose_name_plural': 'История ИИ-поиска',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['project', '-created_at'], name='ai_rag_project_created_idx'),
                ],
            },
        ),
        # HNSW/cosine по проектным чанкам (только PostgreSQL).
        migrations.RunPython(create_project_chunk_hnsw, drop_project_chunk_hnsw),
    ]
