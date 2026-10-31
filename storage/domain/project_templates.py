PROJECT_TEMPLATES = {
    'residential': {
        'name': 'Жилой дом',
        'sections': [
            {'code': 'АР', 'name': 'Архитектурные решения'},
            {'code': 'КР', 'name': 'Конструктивные решения'},
            {'code': 'ОВ', 'name': 'Отопление и вентиляция'},
            {'code': 'ВК', 'name': 'Водоснабжение и канализация'},
        ],
        'folders': [
            {'section_code': 'АР', 'code': '01', 'name': 'Планы'},
            {'section_code': 'АР', 'code': '02', 'name': 'Разрезы'},
            {'section_code': 'КР', 'code': '01', 'name': 'Схемы'},
        ],
    },
}
