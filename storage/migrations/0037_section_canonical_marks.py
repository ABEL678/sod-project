"""Канонизация марок разделов по таблице В.1 ГОСТ Р 21.101-2026.

КР — марка комплекта стадии П (проектная документация), КЖ/КМ — марки
комплектов РД. КР не переименовывается и не сливается с КЖ (уточнение
владельца 25.11.2026): это разные комплекты, а не дубли.

Шаги (идемпотентно, повторный запуск ничего не меняет):
1. Наименования совпадающих кодов приводятся к каноническим.
2. Наименование служебного COMMON меняется на «Общие данные (служебный раздел)».
   Код COMMON не меняется, запись ОД не создаётся.
3. Дополнительные коды (АК, СПА, СС) не трогаются: канонизация — волна 1.
4. full_code разделов и папок, относящихся к изменённым разделам, пересчитываются.

Слияние предусмотрено только для дублей одного и того же кода; на момент
написания таких строк быть не может — (project, code) объявлен
unique_together. Переименования «похожих» кодов (в том числе КР→КЖ) нет.

Миграция только обновляет строки; atomic=False — по уроку 0035/0036.
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


def forwards(apps, schema_editor):
    Project = apps.get_model('storage', 'Project')
    Section = apps.get_model('storage', 'Section')
    Folder = apps.get_model('storage', 'Folder')

    for project in Project.objects.all():
        base_code = project.base_code or ''

        # 1. Канонические наименования совпадающих кодов.
        for section in Section.objects.filter(project=project, code__in=CANON_NAMES.keys()):
            canon = CANON_NAMES[section.code]
            if section.name != canon:
                section.name = canon
                section.save(update_fields=['name'])

        # 2. COMMON: код не меняется, наименование — служебное.
        common = Section.objects.filter(project=project, code=COMMON_CODE).first()
        if common is not None and common.name != COMMON_NAME:
            common.name = COMMON_NAME
            common.save(update_fields=['name'])

        # 3. full_code разделов проекта (после канонизации), затем папок.
        for section in Section.objects.filter(project=project):
            full = _full_code(base_code, section.code)
            if section.full_code != full:
                section.full_code = full
                section.save(update_fields=['full_code'])
            _refresh_folders(Folder, section, base_code)


class Migration(migrations.Migration):

    # Массовые UPDATE строк Section с FK (deferred-триггеры) в одной
    # транзакции дают pending trigger events — см. урок 0035/0036.
    atomic = False

    dependencies = [
        ('storage', '0036_customer_gip_stage'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
