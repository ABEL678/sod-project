from django import forms
from django.db.models import Q

import django_filters

from storage.domain.models import Document, Section


FILTER_FORM_ID = 'filtersForm'


class DocumentFilter(django_filters.FilterSet):
    """Фильтр документов проекта для панели над таблицей."""

    section = django_filters.ModelChoiceFilter(
        field_name='section',
        queryset=Section.objects.exclude(code='COMMON'),
        label='Раздел',
        empty_label='Все разделы',
        widget=forms.Select(attrs={
            'form': FILTER_FORM_ID,
            'class': 'form-select form-select-sm',
            'aria-label': 'Фильтр по разделу',
        }),
    )
    doc_type = django_filters.ChoiceFilter(
        field_name='doc_type',
        choices=Document.DOC_TYPE_CHOICES,
        label='Тип',
        empty_label='Все типы',
        widget=forms.Select(attrs={
            'form': FILTER_FORM_ID,
            'class': 'form-select form-select-sm',
            'aria-label': 'Фильтр по типу документа',
        }),
    )
    status = django_filters.ChoiceFilter(
        field_name='status',
        choices=Document.STATUS_CHOICES,
        label='Статус',
        empty_label='Все статусы',
        widget=forms.Select(attrs={
            'form': FILTER_FORM_ID,
            'class': 'form-select form-select-sm',
            'aria-label': 'Фильтр по статусу',
        }),
    )
    suitability_code = django_filters.ChoiceFilter(
        field_name='suitability_code',
        choices=Document.SUITABILITY_CHOICES,
        # UI-аудит (треб. 1.4): подпись как у колонки «Статус документа»;
        # варианты — текстовые названия из SUITABILITY_CHOICES (без кодов).
        label='Статус документа',
        empty_label='Все статусы',
        widget=forms.Select(attrs={
            'form': FILTER_FORM_ID,
            'id': 'id_suitability_code',
            'class': 'form-select form-select-sm',
            'aria-label': 'Фильтр по статусу для передачи',
        }),
    )
    internal_status = django_filters.ChoiceFilter(
        field_name='internal_status',
        choices=Document.INTERNAL_STATUS_CHOICES,
        label='Внутренний статус',
        # «Все внутренние» (95px) впритык к min-width 130px селекта и
        # обрезалось при сжатии — «Все статусы» (71px) влезает всегда.
        empty_label='Все статусы',
        widget=forms.Select(attrs={
            'form': FILTER_FORM_ID,
            'id': 'id_internal_status',
            'class': 'form-select form-select-sm',
            'aria-label': 'Фильтр по внутреннему статусу',
        }),
    )
    code = django_filters.CharFilter(
        field_name='code',
        lookup_expr='icontains',
        label='Шифр',
        widget=forms.TextInput(attrs={
            'form': FILTER_FORM_ID,
            'class': 'form-control form-control-sm',
            'placeholder': 'Шифр',
            'aria-label': 'Фильтр по шифру',
        }),
    )
    name = django_filters.CharFilter(
        field_name='name',
        lookup_expr='icontains',
        label='Название',
        widget=forms.TextInput(attrs={
            'form': FILTER_FORM_ID,
            'class': 'form-control form-control-sm',
            'placeholder': 'Название',
            'aria-label': 'Фильтр по названию',
        }),
    )
    q = django_filters.CharFilter(
        method='filter_search',
        label='Поиск',
        widget=forms.TextInput(attrs={
            'form': FILTER_FORM_ID,
            'id': 'quickFilter',
            'type': 'search',
            'class': 'form-control form-control-sm',
            'placeholder': 'Поиск по шифру или названию',
            'aria-label': 'Поиск по документам',
        }),
    )

    class Meta:
        model = Document
        fields = [
            'section', 'doc_type', 'status', 'suitability_code',
            'internal_status', 'code', 'name',
        ]

    def filter_search(self, queryset, name, value):
        value = (value or '').strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(code__icontains=value)
            | Q(name__icontains=value)
            | Q(created_by__first_name__icontains=value)
            | Q(created_by__last_name__icontains=value)
            | Q(created_by__username__icontains=value)
        )
