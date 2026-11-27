"""W0 п.1-фикс: чтение review_result и санация записи (SPEC 2.0).

Покрывает:
- геттер suitability_code возвращает ручной вердикт только в зоне WIP;
- фильтр S1/S2/S4 читает review_result (сервис и FilterSet, AND-цепочка);
- колонка «Результат проверки» в таблице и строке ведомости;
- сеттер suitability_code — санкционированный канал записи review_result.
"""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from storage.adapters.filters import DocumentFilter
from storage.adapters.tables import DocumentTable
from storage.domain.models import (
    Company,
    Document,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    _apply_state,
    _document_row,
    get_project_documents_queryset,
    review_result_display,
)

User = get_user_model()


class W0P1TestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Компания W0')
        self.user_admin = User.objects.create_user('admin-w0', password='pass')
        UserProfile.objects.update_or_create(
            user=self.user_admin,
            defaults={'role': 'admin', 'company': self.company},
        )
        self.project = Project.objects.create(
            company=self.company,
            code='W0',
            name='Проект W0',
            status='active',
            created_by=self.user_admin,
        )
        ProjectMembership.objects.update_or_create(
            project=self.project,
            user=self.user_admin,
            defaults={'role_in_project': 'gip'},
        )
        self.section_ar = Section.objects.create(
            project=self.project, code='АР', name='АР', order=1,
        )
        self.section_kr = Section.objects.create(
            project=self.project, code='КР', name='КР', order=2,
        )

    def make_document(
        self,
        code,
        *,
        section=None,
        doc_type='drawing',
        state=Document.STATE_WIP_DRAFT,
        review_result='',
    ):
        return Document.objects.create(
            project=self.project,
            section=section or self.section_ar,
            code=code,
            name=f'Документ {code}',
            doc_type=doc_type,
            state=state,
            review_result=review_result,
            created_by=self.user_admin,
        )


class ReviewResultReadTests(W0P1TestBase):
    def test_wip_manual_verdict_is_read_and_displayed(self):
        document = self.make_document('S1-1', review_result='S1')

        self.assertEqual(document.suitability_code, 'S1')
        self.assertEqual(
            document.get_suitability_code_display(),
            'Для совместной работы',
        )

    def test_manual_verdict_does_not_survive_leaving_wip(self):
        document = self.make_document('S1-2', review_result='S1')

        _apply_state(self.user_admin, document, Document.STATE_SHARED)

        document.refresh_from_db()
        self.assertEqual(document.suitability_code, 'S3')
        self.assertEqual(document.review_result, 'S1')

    def test_setter_writes_review_result_without_state_guard(self):
        document = self.make_document('S2-1')

        document.suitability_code = 'S2'
        document.save(update_fields=['review_result'])

        document.refresh_from_db()
        self.assertEqual(document.state, Document.STATE_WIP_DRAFT)
        self.assertEqual(document.review_result, 'S2')
        self.assertEqual(document.suitability_code, 'S2')

    def test_row_contains_review_result(self):
        document = self.make_document('S4-1', review_result='S4')

        row = _document_row(document)

        self.assertEqual(row['review_result'], 'S4')
        self.assertEqual(row['review_result_display'], 'На утверждении')


class ReviewResultFilterTests(W0P1TestBase):
    def test_service_filter_reads_manual_code_from_review_result(self):
        target = self.make_document('S1-A', review_result='S1')
        self.make_document('S0-A')

        queryset = get_project_documents_queryset(
            self.user_admin,
            self.project,
            {'filters': {'suitability_code': 'S1'}},
        )

        self.assertEqual([doc.pk for doc in queryset], [target.pk])

    def test_filter_set_reads_manual_code_from_review_result(self):
        target = self.make_document('S2-A', review_result='S2')
        self.make_document('S1-B', review_result='S1')

        filterset = DocumentFilter(
            {'suitability_code': 'S2'},
            queryset=Document.objects.filter(project=self.project),
        )

        self.assertEqual([doc.pk for doc in filterset.qs], [target.pk])

    def test_manual_code_combines_with_section_and_doc_type_in_one_select(self):
        target = self.make_document('S1-T', review_result='S1')
        self.make_document(
            'S1-OTHER-SECTION', section=self.section_kr, review_result='S1',
        )
        self.make_document('S1-OTHER-TYPE', doc_type='report', review_result='S1')

        queryset = get_project_documents_queryset(
            self.user_admin,
            self.project,
            {
                'filters': {
                    'suitability_code': 'S1',
                    'section': self.section_ar.pk,
                    'doc_type': 'drawing',
                },
            },
        )

        with CaptureQueriesContext(connection) as captured:
            result = list(queryset)

        # Фильтр по ручному коду (S1) не должен порождать отдельный запрос
        # к документам. С W2-6 в выборку добавлен prefetch состава проекта
        # (project__memberships) — это намеренный второй SELECT для прав
        # строки реестра. Поэтому считаем только запросы к storage_document.
        selects = [
            query for query in captured.captured_queries
            if query['sql'].lstrip().upper().startswith('SELECT')
            and 'storage_document' in query['sql']
        ]
        self.assertEqual(len(selects), 1)
        self.assertEqual([doc.pk for doc in result], [target.pk])


class ReviewResultColumnTests(W0P1TestBase):
    def test_table_column_renders_choice_label_in_wip(self):
        document = self.make_document('S1-TBL', review_result='S1')
        table = DocumentTable([])

        self.assertIn('review_result', table.base_columns)
        self.assertEqual(table.render_review_result(document), 'Для совместной работы')

    def test_table_column_dash_outside_wip_or_empty(self):
        empty = self.make_document('EMPTY')
        shared = self.make_document(
            'SHARED', state=Document.STATE_SHARED, review_result='S1',
        )
        table = DocumentTable([])

        self.assertEqual(table.render_review_result(empty), '—')
        self.assertEqual(table.render_review_result(shared), '—')
        self.assertEqual(review_result_display(shared), '—')
