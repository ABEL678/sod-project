"""Откат ошибочного переименования КР -> КЖ в демо-проекте DEMO-2026 (миграция 0037).

0037 переименовала раздел КР (без коллизии) в КЖ в проекте, где КЖ не было.
Правило: переименование существующих разделов без коллизии не выполняется.
Здесь возвращается прежнее состояние раздела: code='КР',
name='Конструктивные решения', full_code пересчитывается по формуле.

Документы и папки на разделе не переносятся: их FK остаются на месте.
Затрагивается только проект с кодом DEMO-2026. Проект, где КЖ слита (проект 1),
не меняется.
Идемпотентно: если раздела с кодом КЖ в проекте нет или КР уже есть — no-op.
Откат (reverse) — noop.
"""

from django.db import migrations


PROJECT_CODE = 'DEMO-2026'
FROM_CODE = 'КЖ'
TO_CODE = 'КР'
RESTORE_NAME = 'Конструктивные решения'


def _full_code(*parts):
    return '-'.join(p for p in ((v or '').strip() for v in parts) if p)


def _refresh_folders(Folder, section, base_code):
    """Пересчёт full_code папок раздела (та же формула, что в 0037)."""

    def walk(folder, parent_full):
        code = (folder.code or '').strip()
        if code and base_code and code.startswith(f'{base_code}-'):
            full = code
        elif parent_full:
            full = _full_code(parent_full, code) if code else parent_full
        else:
            full = _full_code(base_code, section.code, code)
        if folder.full_code != full:
            folder.full_code = full
            folder.save(update_fields=['full_code'])
        for child in Folder.objects.filter(parent=folder, is_deleted=False):
            walk(child, full)

    for root in Folder.objects.filter(section=section, parent__isnull=True, is_deleted=False):
        walk(root, None)


def forwards(apps, schema_editor):
    Project = apps.get_model('storage', 'Project')
    Section = apps.get_model('storage', 'Section')
    Folder = apps.get_model('storage', 'Folder')

    project = Project.objects.filter(code=PROJECT_CODE).first()
    if project is None:
        return

    # Коллизия КР уже есть — ничего не делаем (идемпотентность).
    if Section.objects.filter(project=project, code=TO_CODE).exists():
        return

    section = Section.objects.filter(project=project, code=FROM_CODE).first()
    if section is None:
        return

    base_code = project.base_code or ''
    section.code = TO_CODE
    section.name = RESTORE_NAME
    section.full_code = _full_code(base_code, TO_CODE)
    section.save(update_fields=['code', 'name', 'full_code'])
    _refresh_folders(Folder, section, base_code)


class Migration(migrations.Migration):

    dependencies = [
        ('storage', '0037_section_canonical_marks'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
