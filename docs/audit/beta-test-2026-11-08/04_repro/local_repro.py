"""Локальное воспроизведение ИИ-контура Stream-CAD (коммит c125f98).

Шаги:
1. Миграции на SQLite, сид правил.
2. Сид: компания, пользователь, проект, раздел, папка, документ+версия (PDF).
3. Прогон правил (run_rules_check) -> находки.
4. confirm_finding через test client (был 500 в локальной версии).
5. finding_preview.
6. Отчёт ГИПу: generate_gip_report + PDF + Word.
7. Негатив: rotation='abc' в confirm_finding (проверка 500).
"""
import io
import os
import pathlib
import sys

os.environ['AI_CHECKS_ENABLED'] = 'True'
os.environ['DJANGO_DEBUG'] = 'True'
os.environ['DJANGO_ALLOWED_HOSTS'] = 'testserver,localhost,127.0.0.1'
os.environ.setdefault('CELERY_TASK_ALWAYS_EAGER', 'True')

BASE = pathlib.Path('/home/z/my-project/sod-project')
sys.path.insert(0, str(BASE))
os.chdir(BASE)

import django  # noqa: E402

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')
django.setup()

from django.core.management import call_command  # noqa: E402

call_command('migrate', verbosity=0, interactive=False)
print('[1] migrate OK')

from django.contrib.auth.models import User  # noqa: E402

from storage.domain.models import (  # noqa: E402
    Company, Document, DocumentVersion, Folder, Project, Section,
)
from storage.domain.services import upload_document  # noqa: E402
from ai_checks.domain.services import (  # noqa: E402
    generate_gip_report, gip_report_docx, gip_report_pdf, run_rules_check,
)
from ai_checks.domain.models import CheckRun, PendingCheckResult  # noqa: E402

# --- сид (идемпотентно: сносим прошлый тест-проект) ---
user, _ = User.objects.get_or_create(
    username='auditor', defaults={'is_superuser': True, 'is_staff': True},
)
user.is_superuser = True
user.set_password('chat_z_ai')
user.save()

Project.objects.filter(code='BETA-01').delete()

from storage.domain.services import get_user_company  # noqa: E402

company, _ = Company.objects.get_or_create(name='Бэта-Компания')
profile = getattr(user, 'userprofile', None)
if profile is None:
    from storage.domain.models import UserProfile
    profile = UserProfile.objects.create(user=user, company=company,
                                         role='admin')
else:
    profile.company = company
    profile.role = 'admin'
    profile.save()

project = Project.objects.create(
    company=company, code='BETA-01', name='Бэта-проект локального прогона',
    customer='Тест', created_by=user,
)
section = Section.objects.create(
    project=project, code='ОВ', name='Отопление и вентиляция', order=1,
)
folder = Folder.objects.create(
    project=project, section=section, name='ОВ-чертежи', created_by=user,
)

# тестовый PDF с плотным содержимым (2 страницы A3-landscape)
from reportlab.pdfgen import canvas as rl_canvas  # noqa: E402

pdf_path = '/tmp/local_test_drawing.pdf'
c = rl_canvas.Canvas(pdf_path, pagesize=(1191, 842))
c.setTitle('local')
for page in range(2):
    c.setFont('Helvetica', 10)
    c.drawString(60, 800, f'Local test drawing page {page + 1}: OV/EOM loads')
    for i in range(60):
        c.drawString(40, 780 - i * 12,
                     f'row {i}: load Q=120 m3/h P=3.5kW cable 5x16 Y-CY')
    c.showPage()
c.save()


class _PdfFile(io.BytesIO):
    """BytesIO с именем/размером (валидация в upload_document)."""

    name = 'local_test_drawing.pdf'

    @property
    def size(self):
        return len(self.getvalue())


from django.core.files.base import ContentFile  # noqa: E402


class _Pdf(ContentFile):
    """ContentFile c расширением .pdf для валидации."""

    def __init__(self, content):
        super().__init__(content, name='local_test_drawing.pdf')


version = upload_document(
    user=user, project=project, section=section,
    code='BETA-OV-01', name='Бэта-чертёж ОВ', doc_type='drawing',
    file=_Pdf(pathlib.Path(pdf_path).read_bytes()),
    comment='локальный прогон бэта-теста',
    folder=folder,
)
document = version.document  # upload_document возвращает DocumentVersion
print(f'[2] сид OK: doc={document.pk} version={version.pk}')

# --- прогон правил ---
run = run_rules_check(version, user)
print(f'[3] прогон: status={run.status} findings={run.findings_total} '
      f'rules={run.rules_total} meta={run.meta}')

findings = list(PendingCheckResult.objects.filter(
    check_run=run).order_by('pk')[:5])
for f in findings:
    print(f'    finding#{f.pk} rule={f.rule.code} page={f.page} '
          f'bbox={f.bbox} msg={f.message[:60]}')

# --- confirm_finding через test client ---
from django.test import Client  # noqa: E402

client = Client()
assert client.login(username='auditor', password='chat_z_ai')
print('[4] login OK')

# берём вью-поворот как это делает кнопка: rotation текущий вьювера
rotation = '0'
ok_result = None
for f in findings:
    if f.page and f.bbox:
        ok_result = f
        break

if ok_result is None:
    print('[4] НЕТ находок с координатами — подтверждение не тестируется')
else:
    url = f'/ai/finding/{ok_result.pk}/confirm/'
    resp = client.post(url, {'rotation': rotation})
    print(f'[4] confirm_finding POST {url} -> HTTP {resp.status_code} '
          f'(ожидался 200, локальный баг был 500)')
    ok_result.refresh_from_db()
    print(f'    статус находки после: {ok_result.status}')
    created_issues = Document.objects.get(pk=document.pk).issues.filter(
        description__startswith='[ИИ]')
    print(f'    создано Issue по [ИИ]: {created_issues.count()}')

    # повторное подтверждение (idempotent ветка)
    resp2 = client.post(url, {'rotation': rotation})
    print(f'[4b] повторный confirm -> HTTP {resp2.status_code}')

    # preview находки
    resp3 = client.get(f'/ai/finding/{ok_result.pk}/preview/')
    print(f'[5] finding_preview -> HTTP {resp3.status_code} '
          f'content-type={resp3["Content-Type"]} '
          f'bytes={len(resp3.getvalue())}')

# --- панель документа ---
resp4 = client.get(f'/ai/document/{document.pk}/panel/')
print(f'[5b] panel -> HTTP {resp4.status_code}')

# --- отчёт ГИПу ---
from datetime import date, timedelta  # noqa: E402

report = generate_gip_report(
    project, date.today() - timedelta(days=30), date.today(), user,
)
print(f'[6] report #{report.pk}: {report.period} payload.total='
      f'{report.payload.get("total_issues")}')

pdf_bytes = gip_report_pdf(report)
pathlib.Path('/tmp/gip_report.pdf').write_bytes(pdf_bytes)
print(f'[6b] gip_report_pdf: {len(pdf_bytes)} bytes')

import pypdfium2 as pdfium  # noqa: E402

try:
    pdf = pdfium.PdfDocument('/tmp/gip_report.pdf')
    text = pdf[0].get_textpage().get_text_range()
    print(f'[6c] PDF открыт: {len(pdf)} стр.; текст стр.1: '
          f'{text[:120]!r}')
except Exception as exc:  # noqa: BLE001
    print(f'[6c] PDF ОШИБКА: {type(exc).__name__}: {exc}')

docx_bytes = gip_report_docx(report)
pathlib.Path('/tmp/gip_report.docx').write_bytes(docx_bytes)
from docx import Document as Docx  # noqa: E402

d = Docx('/tmp/gip_report.docx')
print(f'[6d] DOCX открыт: параграфов={len(d.paragraphs)}, таблиц='
      f'{len(d.tables)}; заголовок: {d.paragraphs[0].text[:80]!r}')

# --- негататив: rotation='abc' ---
resp5 = None
try:
    f2 = PendingCheckResult.objects.filter(
        check_run=run, status=PendingCheckResult.Status.PENDING,
    ).first()
    if f2:
        url2 = f'/ai/finding/{f2.pk}/confirm/'
        resp5 = client.post(url2, {'rotation': 'abc'})
        print(f'[7] confirm c rotation="abc" -> HTTP {resp5.status_code}')
except Exception as exc:  # noqa: BLE001
    print(f'[7] confirm c rotation="abc" -> EXCEPTION {type(exc).__name__}: '
          f'{str(exc)[:120]}')

# --- негататив: битый PDF в open_pdf ---
import io  # noqa: E402

from ai_checks import pdfium_tools  # noqa: E402

broken = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer broken'
try:
    pdfium_tools.open_pdf(broken)
    print('[8] open_pdf(битый) -> открыт (авторемонт сработал или файл валиден)')
except Exception as exc:  # noqa: BLE001
    print(f'[8] open_pdf(битый) -> {type(exc).__name__}: {str(exc)[:80]}')

truncated = pathlib.Path(pdf_path).read_bytes()[:1500]  # обрыв посреди потока
try:
    doc2 = pdfium_tools.open_pdf(truncated)
    print(f'[8b] open_pdf(обрыв) -> ОТКРЫТ, страниц={len(doc2)} '
          f'(авторемонт xref)')
except Exception as exc:  # noqa: BLE001
    print(f'[8b] open_pdf(обрыв) -> {type(exc).__name__}: {str(exc)[:80]}')

print('DONE')
