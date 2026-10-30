from django import forms
from django.db.models import Q

import django_filters

from storage.domain.models import Document, Section


FILTER_FORM_ID = 'filtersForm'


class DocumentFilter(django_filters.FilterSet):
    """Фильтр документов проекта для панели над таблицей."""

    section = django_filters.ModelChoiceFilter(
        field_name='section',
        queryset=Section.objects.all(),
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
        fields = ['section', 'doc_type', 'status', 'code', 'name']

    def filter_search(self, queryset, name, value):
        value = (value or '').strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(code__icontains=value) | Q(name__icontains=value)
        )
