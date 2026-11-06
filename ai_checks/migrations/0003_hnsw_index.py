"""Ф0.2: HNSW-индекс на InternalChunk.embedding (cosine) — только PostgreSQL.

ARCHITECTURE.md 3.2 (индекс HNSW/cosine_ops) и 8.3 (единый кодовый путь
dev = prod через pgvector). На SQLite миграция — no-op: индекс HNSW
поддерживается только PostgreSQL+pgvector; dev-стек поднимается
docker-compose.dev.yml (pgvector/pgvector:pg16).
"""

from django.db import migrations

INDEX_NAME = 'ai_internal_chunk_embedding_hnsw'


def create_hnsw_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('CREATE EXTENSION IF NOT EXISTS vector;')
    schema_editor.execute(
        f'CREATE INDEX IF NOT EXISTS {INDEX_NAME} '
        'ON ai_checks_internalchunk '
        'USING hnsw (embedding vector_cosine_ops) '
        'WITH (m=16, ef_construction=64);'
    )


def drop_hnsw_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(f'DROP INDEX IF EXISTS {INDEX_NAME};')


class Migration(migrations.Migration):

    dependencies = [
        ('ai_checks', '0002_internaldocument_internalchunk_and_more'),
    ]

    operations = [
        migrations.RunPython(create_hnsw_index, drop_hnsw_index),
    ]
