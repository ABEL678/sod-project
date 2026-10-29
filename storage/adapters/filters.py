from django import forms

import django_filters

from storage.domain.models import Document, Section


class DocumentFilter(django_filters.FilterSet):
    """Фильтр документов проекта."""

    section = django_filters.ModelChoiceFilter(
        field_name='section',
        queryset=Section.objects.all(),
        label='Раздел',
        empty_label='Все разделы',
        widget=forms.Select(attrs={
            'class': 'form-select toolbar-select',
            'aria-label': 'Фильтр по разделу',
        }),
    )
    doc_type = django_filters.ChoiceFilter(
        field_name='doc_type',
        choices=Document.DOC_TYPE_CHOICES,
        label='Тип',
        empty_label='Все типы',
        widget=forms.Select(attrs={
            'class': 'form-select toolbar-select',
            'aria-label': 'Фильтр по типу документа',
        }),
    )
    status = django_filters.ChoiceFilter(
        field_name='status',
        choices=Document.STATUS_CHOICES,
        label='Статус',
        empty_label='Все статусы',
        widget=forms.Select(attrs={
            'class': 'form-select toolbar-select',
            'aria-label': 'Фильтр по статусу',
        }),
    )
    code = django_filters.CharFilter(
        field_name='code',
        lookup_expr='icontains',
        label='Шифр',
        widget=forms.TextInput(
            attrs={'class': 'form-control toolbar-select', 'placeholder': 'Шифр'}
        ),
    )
    name = django_filters.CharFilter(
        field_name='name',
        lookup_expr='icontains',
        label='Название',
        widget=forms.TextInput(
            attrs={'class': 'form-control toolbar-select', 'placeholder': 'Название'}
        ),
    )

    class Meta:
        model = Document
        fields = ['section', 'doc_type', 'status', 'code', 'name']
