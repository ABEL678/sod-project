"""Блок 2 бэта-фиксов 2026-11-08: дисциплинарные стандарты СПДС (сид).

ГОСТ Р 21.101-2026 — «зонтичный» стандарт; детальные требования к составу
и оформлению рабочих чертежей живут в дисциплинарных стандартах СПДС.
Сид добавляет заглушки RuleDefinition (is_active=False — правила не
исполняются движком, пока не детализированы; в RULE_CHECKS таких кодов
нет, поэтому run_rules_check их пропускает) и заполняет norm_ref трёх
базовых структурных правил. Список стандартов — по состоянию на 2026 год.
"""

from django.db import migrations

BASE_NORM_REF = 'ГОСТ Р 21.101-2026'

# norm_ref для существующих структурных правил (этап Ф1).
RULE_NORM_REFS = {
    'SPDS.RAME': BASE_NORM_REF,
    'SPDS.STAMP': BASE_NORM_REF,
    'SPDS.FONT': BASE_NORM_REF,
}

_BODY_SUFFIX = (
    ' Правило — заглушка дисциплинарного стандарта: активируется после '
    'детализации требований (is_active=False).'
)

DISCIPLINE_RULES = [
    {
        'code': 'DISC.ELEC.POWER',
        'title': 'Силовое электрооборудование — состав и оформление',
        'norm_ref': 'ГОСТ 21.613-2014',
    },
    {
        'code': 'DISC.ELEC.OUTLIGHT',
        'title': 'Наружное освещение — состав и оформление',
        'norm_ref': 'ГОСТ 21.607-2014',
    },
    {
        'code': 'DISC.ELEC.INLIGHT',
        'title': 'Внутреннее освещение — состав и оформление',
        'norm_ref': 'ГОСТ 21.608-2014',
    },
    {
        'code': 'DISC.GAS.IN',
        'title': 'Внутренние газопроводы — состав и оформление',
        'norm_ref': 'ГОСТ 21.609-2014',
    },
    {
        'code': 'DISC.GAS.OUT',
        'title': 'Наружные газопроводы — состав и оформление',
        'norm_ref': 'ГОСТ 21.610-85',
    },
    {
        'code': 'DISC.AUTO.KIP',
        'title': 'Автоматизация (КИПиА) — состав и оформление',
        'norm_ref': 'ГОСТ 21.408-2013',
    },
    {
        'code': 'DISC.CONST.RC',
        'title': 'Бетонные и железобетонные конструкции — рабочие чертежи',
        'norm_ref': 'ГОСТ 21.501-2018',
    },
    {
        'code': 'DISC.CONST.METAL',
        'title': 'Металлические конструкции — рабочие чертежи',
        'norm_ref': 'ГОСТ 21.502-2016',
    },
    {
        'code': 'DISC.CONST.WOOD',
        'title': 'Деревянные конструкции — рабочие чертежи',
        'norm_ref': 'ГОСТ 21.504-2016',
    },
    {
        'code': 'DISC.ROAD.AUTO',
        'title': 'Автомобильные дороги — рабочие чертежи',
        'norm_ref': 'ГОСТ 21.701-2013',
    },
    {
        'code': 'DISC.ROAD.RAIL',
        'title': 'Железные дороги — рабочие чертежи',
        'norm_ref': 'ГОСТ 21.702-2013',
    },
    {
        'code': 'DISC.ROAD.SYMBOLS',
        'title': 'Условные обозначения (дороги, ЖД пути)',
        'norm_ref': 'ГОСТ 21.207-2013',
    },
    {
        'code': 'DISC.SURVEY.REPORT',
        'title': 'Инженерные изыскания — состав отчётных документов',
        'norm_ref': 'ГОСТ 21.301-2014',
    },
    {
        'code': 'DISC.SURVEY.SYMBOLS',
        'title': 'Инженерные изыскания — условные обозначения',
        'norm_ref': 'ГОСТ 21.709-2011',
    },
    {
        'code': 'DISC.GEN.SPEC',
        'title': 'Спецификации оборудования и изделий',
        'norm_ref': 'ГОСТ 21.110-2013',
    },
    {
        'code': 'DISC.GEN.SYMBOLS',
        'title': 'Условные обозначения общие (СПДС)',
        'norm_ref': 'ГОСТ 21.201-2011',
    },
    {
        'code': 'DISC.GEN.NORMCTRL',
        'title': 'Нормоконтроль — требования к документации',
        'norm_ref': 'ГОСТ 21.002-2014',
    },
    {
        'code': 'DISC.GEN.SKETCH',
        'title': 'Эскизные чертежи общих видов неповторяющихся изделий',
        'norm_ref': 'ГОСТ 21.114-2013',
    },
]


def seed_rules(apps, schema_editor):
    RuleDefinition = apps.get_model('ai_checks', 'RuleDefinition')

    # 1) norm_ref для трёх базовых структурных правил.
    for code, norm_ref in RULE_NORM_REFS.items():
        RuleDefinition.objects.filter(code=code).update(norm_ref=norm_ref)

    # 2) заглушки дисциплинарных стандартов (неактивные).
    for spec in DISCIPLINE_RULES:
        RuleDefinition.objects.update_or_create(
            code=spec['code'],
            defaults={
                'title': spec['title'],
                'body': (
                    f'Состав и оформление рабочей документации по '
                    f"{spec['norm_ref']}.{_BODY_SUFFIX}"
                ),
                'norm_ref': spec['norm_ref'],
                'rule_kind': 'manual',
                'category': 'discipline',
                'severity': 'major',
                'is_active': False,
                'version': 1,
                'tenant_scope': 'global',
                'company': None,
                'project': None,
            },
        )


def unseed_rules(apps, schema_editor):
    RuleDefinition = apps.get_model('ai_checks', 'RuleDefinition')
    RuleDefinition.objects.filter(
        code__in=[rule['code'] for rule in DISCIPLINE_RULES],
    ).delete()
    RuleDefinition.objects.filter(code__in=RULE_NORM_REFS).update(norm_ref='')


class Migration(migrations.Migration):

    dependencies = [
        ('ai_checks', '0011_rule_norm_ref_checkrun_norm_source'),
    ]

    operations = [
        migrations.RunPython(seed_rules, unseed_rules),
    ]
