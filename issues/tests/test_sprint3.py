"""Спринт 3 · зачистка: регрессионные тесты на фиксы команды и сервис прав.

Пять тестов по разделу 4 «Аудита 3.0» (P1-2): formula injection в
Excel-экспорте, права реестра и палитры, scope проверяющего в workflow
и эквивалентность быстрого get_readable_documents и check_read_permission.
"""
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

import openpyxl

from issues.models import Issue
from storage.domain.models import (
    Company,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    check_read_permission,
    create_issue,
    get_readable_documents,
    upload_document,
)


User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class Sprint3SecurityTests(TestCase):
    """Регрессия security-фиксов команды (коммиты 200cf04 и 6c997f2)."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания С3')
        self.other_company = Company.objects.create(name='Чужая С3')

        self.gip = User.objects.create_user('gip_s3', password='pass')
        UserProfile.objects.update_or_create(user=self.gip, defaults={'role': 'gip', 'company': self.company})[0]

        # проверяющий компании, НЕ состоящий в проекте (scope-фикс)
        self.checker = User.objects.create_user('checker_s3', password='pass')
        UserProfile.objects.update_or_create(user=self.checker, defaults={'role': 'checker', 'company': self.company,
        })[0]

        # подрядчик, допущенный только к разделу АР (фикс прав реестра/палитры)
        self.contractor = User.objects.create_user('contractor_s3', password='pass')
        self.contractor_profile = UserProfile.objects.update_or_create(user=self.contractor, defaults={'role': 'contractor', 'company': self.company,
        })[0]

        self.admin_other = User.objects.create_user('admin_other_s3', password='pass')
        UserProfile.objects.update_or_create(user=self.admin_other, defaults={'role': 'admin', 'company': self.other_company,
        })[0]

        self.project = Project.objects.create(
            company=self.company,
            code='S3-1',
            name='Проект зачистки',
            status='active',
            gip=self.gip,
            created_by=self.gip,
        )
        self.section_ar = Section.objects.create(
            project=self.project, code='АР', name='Архитектура', order=1,
        )
        self.section_kr = Section.objects.create(
            project=self.project, code='КР', name='Конструктив', order=2,
        )

        self.doc_ar = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section_ar,
            code='S3-AR-01',
            name='План АР',
            doc_type='drawing',
            file=SimpleUploadedFile('ar.pdf', b'data', content_type='application/pdf'),
        )
        self.doc_kr = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section_kr,
            code='S3-KR-01',
            name='Схема КР',
            doc_type='drawing',
            file=SimpleUploadedFile('kr.pdf', b'data', content_type='application/pdf'),
        )

        self.contractor_profile.allowed_sections.add(self.section_ar)

    # --- 1. formula injection в Excel-экспорте --------------------------------

    def test_excel_export_escapes_formula_injection(self):
        """Ячейка с '=HYPERLINK(...)' экспортируется как текст, не формула."""
        create_issue(
            user=self.gip,
            document=self.doc_ar.document,
            description='=HYPERLINK("http://evil.example","взлом")',
        )
        self.client.force_login(self.gip)
        response = self.client.get(
            reverse('issues:issue_registry_export', args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 200)

        content = b''.join(response.streaming_content)
        workbook = openpyxl.load_workbook(BytesIO(content))
        sheet = workbook.active
        # колонка 5 — «Текст замечания», первая строка данных — row 2
        value = sheet.cell(row=2, column=5).value
        self.assertIsInstance(value, str)
        self.assertTrue(
            value.startswith("'"),
            'опасная формула не экранирована: {0!r}'.format(value),
        )

    # --- 2. права реестра ------------------------------------------------------

    def test_registry_hides_issues_on_unreadable_documents(self):
        """Подрядчик видит замечания только по разрешённым разделам."""
        visible = create_issue(
            user=self.gip, document=self.doc_ar.document,
            description='REGVIS-AR-marker',
        )
        hidden = create_issue(
            user=self.gip, document=self.doc_kr.document,
            description='REGHIDE-KR-marker',
        )

        self.client.force_login(self.contractor)
        response = self.client.get(
            reverse('issues:issue_registry', args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'REGVIS-AR-marker')
        self.assertNotContains(response, 'REGHIDE-KR-marker')

        # и в Excel-выгрузке тем же правом
        export = self.client.get(
            reverse('issues:issue_registry_export', args=[self.project.pk]),
        )
        sheet = openpyxl.load_workbook(
            BytesIO(b''.join(export.streaming_content)),
        ).active
        texts = [
            sheet.cell(row=row, column=5).value or ''
            for row in range(2, sheet.max_row + 1)
        ]
        self.assertIn(visible.description, texts)
        self.assertNotIn(hidden.description, texts)

    # --- 3. права палитры ------------------------------------------------------

    def test_palette_hides_unreadable_documents(self):
        """Ctrl+K не показывает документы, которые нельзя читать."""
        self.client.force_login(self.contractor)

        allowed = self.client.get(
            reverse('storage:palette_search'), {'q': 'S3-AR'},
        )
        self.assertEqual(allowed.status_code, 200)
        titles_allowed = [d['title'] for d in allowed.json()['documents']]
        self.assertTrue(
            any(t.startswith('S3-AR-01') for t in titles_allowed),
            'читаемый документ не найден в палитре: {0!r}'.format(titles_allowed),
        )

        hidden = self.client.get(
            reverse('storage:palette_search'), {'q': 'S3-KR'},
        )
        self.assertEqual(hidden.status_code, 200)
        titles_hidden = [d['title'] for d in hidden.json()['documents']]
        self.assertFalse(
            any(t.startswith('S3-KR-01') for t in titles_hidden),
            'закрытый документ утёк в палитру: {0!r}'.format(titles_hidden),
        )

    # --- 4. scope проверяющего в workflow --------------------------------------

    def test_workflow_checker_without_membership_is_denied(self):
        """Проверяющий без членства в проекте не управляет зонами (403)."""
        url = reverse(
            'storage:document_workflow', args=[self.doc_ar.document.pk],
        )
        self.client.force_login(self.checker)
        response = self.client.post(url, {'action': 'return_wip'})
        self.assertEqual(response.status_code, 403)

        self.doc_ar.document.refresh_from_db()
        folder = self.doc_ar.document.folder
        zone_before = folder.zone if folder else None

        # и после отказа зона не изменилась
        response2 = self.client.post(url, {'action': 'submit_review'})
        self.assertIn(response2.status_code, (403, 400))
        self.doc_ar.document.refresh_from_db()
        folder_after = self.doc_ar.document.folder
        zone_after = folder_after.zone if folder_after else None
        self.assertEqual(zone_before, zone_after)

        # положительный контроль: ГИП проекта действует
        # (T67: подача на согласование требует статуса «Готов к передаче»)
        self.client.force_login(self.gip)
        self.doc_ar.document.internal_status = 'ready_to_share'
        self.doc_ar.document.save()
        ok = self.client.post(url, {'action': 'submit_review'})
        self.assertEqual(ok.status_code, 200)

    # --- 5. эквивалентность сервиса прав ---------------------------------------

    def test_get_readable_documents_matches_check_read_permission(self):
        """Быстрый сервис прав даёт тот же результат, что по-документная проверка.

        Роль за ролью: gip, checker (без членства), contractor (ограничен
        разделами), админ чужой компании. Спринт 3 снял N+1 с этой функции —
        правила должны были не поехать.
        """
        all_documents = [
            self.doc_ar.document,
            self.doc_kr.document,
        ]
        users = [self.gip, self.checker, self.contractor, self.admin_other]

        for user in users:
            expected = {
                doc.pk for doc in all_documents
                if check_read_permission(user, doc)
            }
            actual = set(
                get_readable_documents(user).values_list('pk', flat=True),
            )
            self.assertEqual(
                expected,
                actual,
                'расхождение прав для {0} (role={1})'.format(
                    user.username,
                    getattr(user.userprofile, 'role', None),
                ),
            )

        # и через проектную выборку — реестр фильтрует именно так
        for user in users:
            expected = {
                doc.pk for doc in all_documents
                if check_read_permission(user, doc)
            }
            actual = set(
                get_readable_documents(
                    user, project=self.project,
                ).values_list('pk', flat=True),
            )
            self.assertEqual(expected, actual)
