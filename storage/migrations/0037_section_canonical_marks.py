"""Канонизация марок разделов по таблице В.1 ГОСТ Р 21.101-2026.

Шаги (идемпотентно, повторный запуск ничего не меняет):
1. Наименования совпадающих кодов приводятся к каноническим.
2. КР -> КЖ: если в проекте есть КЖ, все ссылки с КР переносятся на КЖ и
   пустая КР удаляется (слияние). Если КЖ нет — код КР переименовывается в КЖ.
3. Наименование служебного COMMON меняется на «Общие данные (служебный раздел)».
   Код COMMON не меняется, запись ОД не создаётся.
4. Дополнительные коды (АК, СПА, СС) не трогаются: канонизация — волна 1.
5. full_code разделов и папок, относящихся к изменённым разделам, пересчитываются.

Миграция только обновляет и удаляет строки; atomic=False — по уроку 0036.
"""

from django.db import migrations


CANON_NAMES = {
    'АР': 'Архитектурные решения',
    'КЖ': 'Конструкции железобетонные',
    'КМ': 'Конструкции металлические',
    'ОВ': 'Отопление, вентиляция и кондиционирование',
    'ВК': 'Внутренние системы водоснабжения и канализации',
    'ЭОМ': 'Силовое электрооборудование и электрическое освещение (внутреннее)',
    'ТХ': 'Технология производства',
}
COMMON_CODE = 'COMMON'
COMMON_NAME = 'Общие данные (служебный раздел)'
MERGE_FROM = 'КР'
MERGE_TO = 'КЖ'
MERGE_TO_NAME = CANON_NAMES[MERGE_TO]


def _full_code(*parts):
    """Как services.generate_gost_code: непустые части через дефис."""
    return '-'.join(p for p in ((v or '').strip() for v in parts) if p)


def _refresh_folders(Folder, section, base_code):
    """Пересчёт full_code папок раздела.

    Корень: base-section.code-code (как services._folder_full_code).
    Подпапка: parent.full_code-code. Абсолютный шифр (начинается с base-) не трогаем.
    """

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


def _move_references(apps, old, new):
    """Переносит все ссылки Section old на Section new (до удаления old)."""
    Document = apps.get_model('storage', 'Document')
    Folder = apps.get_model('storage', 'Folder')
    MailRule = apps.get_model('storage', 'MailRule')
    TaskTemplate = apps.get_model('storage', 'TaskTemplate')
    DesignTask = apps.get_model('storage', 'DesignTask')
    UserProfile = apps.get_model('storage', 'UserProfile')

    Document.objects.filter(section=old).update(section=new)
    Folder.objects.filter(section=old).update(section=new)
    MailRule.objects.filter(section=old).update(section=new)
    TaskTemplate.objects.filter(source_section=old).update(source_section=new)
    TaskTemplate.objects.filter(target_section=old).update(target_section=new)
    DesignTask.objects.filter(source_section=old).update(source_section=new)
    DesignTask.objects.filter(target_section=old).update(target_section=new)

    Through = UserProfile.allowed_sections.through
    for row in Through.objects.filter(section_id=old.pk):
        if not Through.objects.filter(userprofile_id=row.userprofile_id, section_id=new.pk).exists():
            Through.objects.create(userprofile_id=row.userprofile_id, section_id=new.pk)
    Through.objects.filter(section_id=old.pk).delete()


def forwards(apps, schema_editor):
    Project = apps.get_model('storage', 'Project')
    Section = apps.get_model('storage', 'Section')
    Folder = apps.get_model('storage', 'Folder')

    for project in Project.objects.all():
        base_code = project.base_code or ''

        # 2. КР -> КЖ: слияние или переименование кода.
        old = Section.objects.filter(project=project, code=MERGE_FROM).first()
        if old is not None:
            new = Section.objects.filter(project=project, code=MERGE_TO).first()
            if new is not None:
                _move_references(apps, old, new)
                old.delete()
                new.name = MERGE_TO_NAME
                new.save(update_fields=['name'])
                affected = new
            else:
                old.code = MERGE_TO
                old.name = MERGE_TO_NAME
                old.save(update_fields=['code', 'name'])
                affected = old
            affected.full_code = _full_code(base_code, affected.code)
            affected.save(update_fields=['full_code'])
            # full_code (ниже) пересчитывается для всех разделов проекта.
            _refresh_folders(Folder, affected, base_code)

        # 1. Канонические наименования совпадающих кодов.
        for section in Section.objects.filter(project=project, code__in=CANON_NAMES.keys()):
            canon = CANON_NAMES[section.code]
            if section.name != canon:
                section.name = canon
                section.save(update_fields=['name'])

        # 3. COMMON: код не меняется, наименование — служебное.
        common = Section.objects.filter(project=project, code=COMMON_CODE).first()
        if common is not None and common.name != COMMON_NAME:
            common.name = COMMON_NAME
            common.save(update_fields=['name'])

        # 4. full_code разделов проекта (после переименований), затем папок.
        for section in Section.objects.filter(project=project):
            full = _full_code(base_code, section.code)
            if section.full_code != full:
                section.full_code = full
                section.save(update_fields=['full_code'])
            _refresh_folders(Folder, section, base_code)


class Migration(migrations.Migration):

    # Массовые UPDATE и DELETE строк Section с FK (deferred-триггеры) в одной
    # транзакции дают pending trigger events — см. урок 0036.
    atomic = False

    dependencies = [
        ('storage', '0036_customer_gip_stage'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
