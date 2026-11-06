"""Ф1: сид первых трёх детерминированных правил СПДС (idempotent).

Structural-правила исполняются движком по code (domain/rules.py);
rule_kind='manual' — спековая классификация 3.2 (regex/keyword/manual),
маркер «не regex/keyword-правило».
"""

from django.db import migrations

RULES = [
    {
        'code': 'SPDS.RAME',
        'title': 'Рамка листа',
        'body': (
            'Каждый лист должен иметь внешнюю рамку: длинные линии по '
            'верхнему/нижнему и левому/правому краям (СПДС).'
        ),
        'rule_kind': 'manual',
        'category': 'frame',
        'severity': 'major',
    },
    {
        'code': 'SPDS.STAMP',
        'title': 'Основная надпись',
        'body': (
            'В правом нижнем углу листа должна быть основная надпись '
            '(штамп СПДС): текст «Основная надпись» или рамка штампа.'
        ),
        'rule_kind': 'manual',
        'category': 'stamp',
        'severity': 'major',
    },
    {
        'code': 'SPDS.FONT',
        'title': 'Минимальная высота шрифта',
        'body': (
            'Высота шрифта основного текста — не менее 2.5 мм '
            '(≈ 7.09 pt); надписи штампа не проверяются.'
        ),
        'rule_kind': 'manual',
        'category': 'fonts',
        'severity': 'minor',
    },
]


def seed_rules(apps, schema_editor):
    RuleDefinition = apps.get_model('ai_checks', 'RuleDefinition')
    for rule in RULES:
        RuleDefinition.objects.update_or_create(
            code=rule['code'],
            defaults={
                'title': rule['title'],
                'body': rule['body'],
                'rule_kind': rule['rule_kind'],
                'category': rule['category'],
                'severity': rule['severity'],
                'is_active': True,
                'version': 1,
                'tenant_scope': 'global',
                'company': None,
                'project': None,
            },
        )


def unseed_rules(apps, schema_editor):
    RuleDefinition = apps.get_model('ai_checks', 'RuleDefinition')
    RuleDefinition.objects.filter(
        code__in=[rule['code'] for rule in RULES],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('ai_checks', '0005_ruledefinition_pendingcheckresult_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_rules, unseed_rules),
    ]
