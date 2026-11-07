"""Создаёт демонстрационный проект для бета-тестирования.

Команда наполняет СОД реалистичным набором данных: проект по шаблону FULL,
разделы АР/КР/ОВ/ВК, папки, документы с версиями, замечания в разных
статусах, зоны WIP/SHARED/PUBLISHED/ARCHIVE и публичную share-ссылку.

Все доменные сущности создаются через сервисы storage.domain.services.
Исключения — bootstrap компании/пользователей и членство в проекте:
отдельных сервисов для этих операций в контракте пока нет.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from issues.models import Issue
from storage.adapters.local_storage import LocalFileStorage
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Folder,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    add_company_member,
    create_folder,
    create_issue,
    create_project_from_template,
    create_section,
    delete_documents,
    delete_project,
    empty_trash,
    generate_share_link,
    update_issue_status,
    upload_document,
)


User = get_user_model()

DEMO_CODE = 'DEMO-2026'
DEMO_NAME = 'Демо-проект ЖК Солнечный'
DEMO_CUSTOMER = 'ООО Застройщик'
DEMO_BASE_CODE = '2345-12'
DEMO_TEMPLATE = 'FULL'
DEMO_PASSWORD = 'demo12345'

SECTION_SPECS = (
    ('АР', 'Архитектурные решения', 'Планы'),
    ('КР', 'Конструктивные решения', 'Конструкции'),
    ('ОВ', 'Отопление и вентиляция', 'Вентиляция'),
    ('ВК', 'Водоснабжение и канализация', 'Водоснабжение'),
)


class Command(BaseCommand):
    help = (
        'Создаёт демонстрационный проект DEMO-2026 с документами, '
        'версиями, замечаниями и share-ссылкой.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--company-id',
            type=int,
            default=None,
            help='ID компании, куда создать демо-проект.',
        )
        parser.add_argument(
            '--user-username',
            default=None,
            help='Пользователь-создатель (роль admin или gip).',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Удалить существующий DEMO-2026 и создать заново.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help=(
                'Разрешить запуск в продакшене (DJANGO_DEBUG=False). '
                'Команда создаёт аккаунты с известным паролем '
                f'«{DEMO_PASSWORD}» — без --force в проде она отклоняется.'
            ),
        )

    def handle(self, *args, **options):
        # Спринт 7: guard от запуска в продакшене. Демо-аккаунты создаются
        # с публично известным паролем и ролью admin — на боевом сервере
        # это прямой обход аутентификации.
        if not settings.DEBUG and not options.get('force'):
            raise CommandError(
                'create_demo_project создаёт аккаунты с известным паролем '
                f'«{DEMO_PASSWORD}» и ролью admin. В продакшене '
                '(DJANGO_DEBUG=False) это небезопасно: передайте --force, '
                'если вы действительно хотите это сделать, и смените '
                'пароли сразу после демо.'
            )

        self._created_files: list[str] = []
        self._accounts: list[dict] = []

        try:
            with transaction.atomic():
                result = self._create_demo(
                    company_id=options['company_id'],
                    username=options['user_username'],
                    reset=options['reset'],
                )
        except Exception:
            storage = LocalFileStorage()
            for path in self._created_files:
                storage.delete(path)
            raise

        self._write_report(result)

    # ------------------------------------------------------------------
    # Сценарий создания
    # ------------------------------------------------------------------

    def _create_demo(self, *, company_id: int | None, username: str | None, reset: bool) -> dict:
        company, company_created = self._resolve_company(company_id)
        maintainer = self._resolve_maintainer(company)
        actor = self._resolve_actor(company, username, maintainer)

        existing = Project.objects.filter(code=DEMO_CODE).first()
        if existing is not None and not reset:
            raise CommandError(
                'Демо-проект уже существует. Используйте --reset для пересоздания.'
            )
        if existing is not None:
            self._remove_existing(existing, company, maintainer)

        gip = self._resolve_gip(company, actor, maintainer)
        project = create_project_from_template(
            company=company,
            code=DEMO_CODE,
            name=DEMO_NAME,
            customer=DEMO_CUSTOMER,
            gip=gip,
            created_by=actor,
            template_key=DEMO_TEMPLATE,
            base_code=DEMO_BASE_CODE,
        )

        sections = {}
        for index, (code, name, _folder_name) in enumerate(SECTION_SPECS, start=1):
            sections[code] = create_section(
                project,
                code,
                name,
                index,
                user=actor,
            )

        folders = {}
        for code, _name, folder_name in SECTION_SPECS:
            folders[code] = create_folder(
                actor,
                project,
                name=folder_name,
                section=sections[code],
            )

        checker, checker_created = self._select_or_create_user(
            company, 'checker', 'demo_checker', maintainer
        )
        developer, developer_created = self._select_or_create_user(
            company, 'developer', 'demo_developer', maintainer
        )
        # Developer пишет в WIP только в разрешённые разделы.
        add_company_member(
            maintainer,
            company,
            developer,
            'developer',
            allowed_sections=list(sections.values()),
        )
        self._ensure_project_membership(project, checker, 'checker')
        self._ensure_project_membership(project, developer, 'developer')

        wip_folder = folders['АР']
        shared_zone = self._zone_folder(project, 'SHARED')
        published_zone = self._zone_folder(project, 'PUBLISHED')
        archive_zone = self._zone_folder(project, 'ARCHIVE')

        plan_v1 = self._upload(
            maintainer,
            project,
            sections['АР'],
            code='2345-12-АР-01',
            name='План 1 этажа',
            doc_type='drawing',
            filename='plan_1_floor_v1.pdf',
            text='Demo document: Plan 1 floor, version 1',
            comment='Первая версия',
            folder=wip_folder,
        )
        plan_v2 = self._upload(
            maintainer,
            project,
            sections['АР'],
            code='2345-12-АР-01',
            name='План 1 этажа',
            doc_type='drawing',
            filename='plan_1_floor_v2.pdf',
            text='Demo document: Plan 1 floor, version 2',
            comment='Исправлены размеры',
            folder=wip_folder,
        )
        plan_doc = plan_v2.document

        facades = self._upload(
            maintainer,
            project,
            sections['АР'],
            code='2345-12-АР-02',
            name='Фасады',
            doc_type='drawing',
            filename='facades.pdf',
            text='Demo document: Facades',
            folder=shared_zone,
        ).document
        foundation = self._upload(
            maintainer,
            project,
            sections['КР'],
            code='2345-12-КР-01',
            name='Схема фундамента',
            doc_type='drawing',
            filename='foundation_scheme.pdf',
            text='Demo document: Foundation scheme',
            folder=published_zone,
        ).document
        ventilation = self._upload(
            maintainer,
            project,
            sections['ОВ'],
            code='2345-12-ОВ-01',
            name='План вентиляции',
            doc_type='drawing',
            filename='ventilation_plan.pdf',
            text='Demo document: Ventilation plan',
            folder=archive_zone,
        ).document

        today = timezone.localdate()
        create_issue(
            checker,
            plan_doc,
            'Проверить размеры помещения на плане 1 этажа.',
            version=plan_doc.current_version,
            page_number=1,
            x_ratio=0.3,
            y_ratio=0.4,
            assignee=developer,
            due_date=today + timedelta(days=3),
        )

        in_progress = create_issue(
            checker,
            plan_doc,
            'Уточнить привязку дверного проёма.',
            version=plan_doc.current_version,
            assignee=developer,
            due_date=today - timedelta(days=2),
        )
        update_issue_status(developer, in_progress, 'IN_PROGRESS')

        done = create_issue(
            checker,
            foundation,
            'Проверить армирование фундаментной плиты.',
            version=foundation.current_version,
            assignee=developer,
        )
        update_issue_status(developer, done, 'IN_PROGRESS')
        update_issue_status(developer, done, 'DONE')

        closed = create_issue(
            checker,
            ventilation,
            'Проверить расход воздуха по помещению.',
            version=ventilation.current_version,
            assignee=developer,
        )
        update_issue_status(checker, closed, 'CLOSED')

        create_issue(
            checker,
            facades,
            'Уточнить цветовое решение фасада.',
            version=facades.current_version,
            due_date=today + timedelta(days=7),
        )

        discussion = create_issue(
            developer,
            facades,
            'Обсудить узел крепления навесного фасада.',
            version=facades.current_version,
            assignee=developer,
        )
        update_issue_status(developer, discussion, 'DISCUSSION')

        token = generate_share_link(maintainer, plan_doc)
        share_url = (
            reverse('storage:document_detail', args=[plan_doc.pk])
            + '?'
            + urlencode({'share': token})
        )

        return {
            'company': company,
            'company_created': company_created,
            'project': project,
            'sections': sections,
            'documents': Document.objects.filter(project=project).count(),
            'versions': DocumentVersion.objects.filter(
                document__project=project
            ).count(),
            'issues': Issue.objects.filter(document__project=project).count(),
            'plan_doc': plan_doc,
            'share_url': share_url,
            'tokens': token,
            'actor': actor,
            'gip': gip,
            'checker': checker,
            'checker_created': checker_created,
            'developer': developer,
            'developer_created': developer_created,
        }

    # ------------------------------------------------------------------
    # Компания, пользователи, роли
    # ------------------------------------------------------------------

    def _resolve_company(self, company_id: int | None) -> tuple[Company, bool]:
        if company_id is not None:
            company = Company.objects.filter(pk=company_id).first()
            if company is None:
                raise CommandError(f'Компания с ID {company_id} не найдена.')
            return company, False

        company = Company.objects.order_by('pk').first()
        if company is not None:
            return company, False

        company = Company.objects.create(name='Демо-компания')
        return company, True

    def _resolve_maintainer(self, company: Company) -> User:
        superuser = (
            User.objects.filter(is_superuser=True, is_active=True)
            .order_by('pk')
            .first()
        )
        if superuser is not None:
            return superuser

        admin_profile = (
            UserProfile.objects.filter(
                company=company,
                role='admin',
                user__is_active=True,
            )
            .select_related('user')
            .order_by('pk')
            .first()
        )
        if admin_profile is not None:
            return admin_profile.user

        return self._ensure_demo_admin(company)

    def _resolve_actor(
        self,
        company: Company,
        username: str | None,
        maintainer: User,
    ) -> User:
        if username:
            user = User.objects.filter(username=username).first()
            if user is None:
                raise CommandError(f'Пользователь «{username}» не найден.')
            if user.is_superuser:
                return user

            profile = getattr(user, 'userprofile', None)
            if profile is None:
                raise CommandError(
                    f'У пользователя «{username}» нет профиля. '
                    'Назначьте ему роль admin или gip в управлении компанией.'
                )
            if profile.role not in ('admin', 'gip'):
                raise CommandError(
                    f'Пользователь «{username}» должен иметь роль admin или gip.'
                )
            if profile.company_id is None:
                add_company_member(maintainer, company, user, profile.role)
            elif profile.company_id != company.pk:
                raise CommandError(
                    f'Пользователь «{username}» принадлежит другой компании.'
                )
            return user

        superuser = (
            User.objects.filter(is_superuser=True, is_active=True)
            .order_by('pk')
            .first()
        )
        return superuser or maintainer

    def _resolve_gip(
        self,
        company: Company,
        actor: User,
        maintainer: User,
    ) -> User:
        profile = getattr(actor, 'userprofile', None)
        if profile is not None and profile.role == 'gip':
            return actor

        gip_profile = (
            UserProfile.objects.filter(
                company=company,
                role='gip',
                user__is_active=True,
            )
            .exclude(user=actor)
            .select_related('user')
            .order_by('pk')
            .first()
        )
        if gip_profile is not None:
            if not any(
                item['username'] == gip_profile.user.username
                for item in self._accounts
            ):
                self._remember_account(gip_profile.user, 'gip', None)
            return gip_profile.user

        gip, _created = self._ensure_demo_user(
            company, 'gip', 'demo_gip', maintainer
        )
        return gip

    def _ensure_demo_admin(self, company: Company) -> User:
        user = User.objects.filter(username='demo_admin').first()
        created = False
        if user is None:
            user = User.objects.create_user(
                username='demo_admin',
                password=DEMO_PASSWORD,
                first_name='Демо',
                last_name='Администратор',
            )
            created = True
            self._remember_account(user, 'admin', DEMO_PASSWORD)

        profile = getattr(user, 'userprofile', None)
        if profile is None:
            UserProfile.objects.create(user=user, company=company, role='admin')
        elif profile.company_id not in (None, company.pk):
            raise CommandError(
                'Пользователь demo_admin принадлежит другой компании.'
            )
        elif profile.role != 'admin':
            profile.role = 'admin'
            profile.company = company
            profile.save(update_fields=['role', 'company'])
        elif profile.company_id is None:
            profile.company = company
            profile.save(update_fields=['company'])

        if not created and not any(
            item['username'] == user.username for item in self._accounts
        ):
            self._remember_account(user, 'admin', None)
        return user

    def _select_or_create_user(
        self,
        company: Company,
        role: str,
        demo_username: str,
        maintainer: User,
    ) -> tuple[User, bool]:
        profile = (
            UserProfile.objects.filter(
                company=company,
                role=role,
                user__is_active=True,
            )
            .select_related('user')
            .order_by('pk')
            .first()
        )
        if profile is not None:
            if not any(
                item['username'] == profile.user.username
                for item in self._accounts
            ):
                self._remember_account(profile.user, role, None)
            return profile.user, False
        return self._ensure_demo_user(company, role, demo_username, maintainer)

    def _ensure_demo_user(
        self,
        company: Company,
        role: str,
        username: str,
        maintainer: User,
    ) -> tuple[User, bool]:
        user = User.objects.filter(username=username).first()
        created = False
        if user is None:
            user = User.objects.create_user(
                username=username,
                password=DEMO_PASSWORD,
                first_name='Демо',
                last_name=role,
            )
            created = True

        profile = getattr(user, 'userprofile', None)
        if profile is not None and profile.company_id not in (None, company.pk):
            raise CommandError(
                f'Пользователь {username} уже принадлежит другой компании.'
            )
        if (
            profile is not None
            and profile.company_id == company.pk
            and profile.role != role
        ):
            raise CommandError(
                f'Пользователь {username} уже существует с ролью '
                f'«{profile.get_role_display()}», ожидалась «{role}».'
            )

        try:
            add_company_member(maintainer, company, user, role)
        except PermissionDenied as exc:
            raise CommandError(str(exc)) from exc

        if created:
            self._remember_account(user, role, DEMO_PASSWORD)
        elif not any(item['username'] == user.username for item in self._accounts):
            self._remember_account(user, role, None)
        return user, created

    def _remember_account(self, user: User, role: str, password: str | None) -> None:
        self._accounts.append({
            'username': user.username,
            'role': role,
            'password': password,
        })

    def _ensure_project_membership(
        self,
        project: Project,
        user: User,
        role: str,
    ) -> ProjectMembership:
        # В services.py пока нет add_project_member: членство создаётся
        # здесь как fixture-операция, без доменной бизнес-логики.
        membership, _created = ProjectMembership.objects.update_or_create(
            project=project,
            user=user,
            defaults={'role_in_project': role},
        )
        return membership

    # ------------------------------------------------------------------
    # Удаление старого демо-проекта
    # ------------------------------------------------------------------

    def _remove_existing(
        self,
        project: Project,
        company: Company,
        maintainer: User,
    ) -> None:
        if project.company_id != company.pk:
            raise CommandError(
                f'Проект {DEMO_CODE} уже существует в компании '
                f'«{project.company.name}» (ID {project.company_id}). '
                'Укажите --company-id этой компании для пересоздания.'
            )

        try:
            # Сначала мягко удаляем документы и очищаем корзину, пока
            # проект ещё активен: empty_trash() требует доступа к проекту.
            active_documents = Document.objects.filter(
                project=project,
                is_deleted=False,
            )
            if active_documents.exists():
                delete_documents(maintainer, active_documents)
            empty_trash(maintainer, project, LocalFileStorage())

            # Затем штатное мягкое удаление проекта.
            delete_project(maintainer, project)
        except PermissionDenied as exc:
            raise CommandError(
                f'Не удалось удалить старый демо-проект: {exc}'
            ) from exc

        # Hard delete нужен, потому что create_project() восстанавливает
        # мягко удалённый проект с тем же шифром и не разворачивает шаблон.
        project.delete()

    # ------------------------------------------------------------------
    # Создание файлов и сущностей
    # ------------------------------------------------------------------

    def _zone_folder(self, project: Project, zone: str) -> Folder:
        folder = Folder.objects.filter(
            project=project,
            parent__isnull=True,
            section__isnull=True,
            is_system_zone=True,
            zone=zone,
        ).first()
        if folder is None:
            raise CommandError(
                f'В проекте не найдена системная зона {zone}.'
            )
        return folder

    def _upload(
        self,
        user: User,
        project: Project,
        section: Section,
        *,
        code: str,
        name: str,
        doc_type: str,
        filename: str,
        text: str,
        comment: str = '',
        folder: Folder | None = None,
    ):
        version = upload_document(
            user,
            project,
            section,
            code,
            name,
            doc_type,
            self._pdf(filename, text),
            comment=comment,
            folder=folder,
        )
        self._created_files.append(version.file.name)
        return version

    @staticmethod
    def _pdf(filename: str, text: str) -> SimpleUploadedFile:
        """Минимальный валидный PDF A4 через reportlab (замена PyMuPDF)."""
        from reportlab.pdfgen import canvas as rl_canvas

        buffer = BytesIO()
        canvas = rl_canvas.Canvas(buffer, pagesize=(595, 842))
        # точка вставки (72, 72) в PyMuPDF — TL-система; в reportlab — BL:
        # y = 842 - 72. Текст без встроенной кириллицы (base14 Helvetica),
        # как и раньше (PyMuPDF 'helv').
        canvas.setFont('Helvetica', 14)
        canvas.drawString(72, 842 - 72, text)
        canvas.showPage()
        canvas.save()
        payload = buffer.getvalue()
        return SimpleUploadedFile(
            filename,
            payload,
            content_type='application/pdf',
        )

    # ------------------------------------------------------------------
    # Отчёт
    # ------------------------------------------------------------------

    def _write_report(self, result: dict) -> None:
        project = result['project']
        project_url = reverse('storage:project_detail', args=[project.pk])
        accounts = ', '.join(
            f"{item['username']} / {item['password']}"
            if item['password']
            else f"{item['username']} (существующий)"
            for item in self._accounts
        ) or '—'

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ Демо-проект создан: {project.code} «{project.name}»'
            )
        )
        self.stdout.write(f'   URL: {project_url}')
        self.stdout.write(
            f"   Документов: {result['documents']}, "
            f"версий: {result['versions']}, "
            f"замечаний: {result['issues']}"
        )
        self.stdout.write(f'   Пользователи: {accounts}')
        self.stdout.write(f"   Создатель: {result['actor'].username}")
        self.stdout.write(f"   ГИП: {result['gip'].username}")
        self.stdout.write(
            f"   Share-ссылка: {result['share_url']} "
            f"(документ «{result['plan_doc'].name}»)"
        )
        self.stdout.write(
            '   Примечание: в текущей маршрутизации публичная ссылка имеет '
            'вид /documents/<id>/?share=<token>; отдельного /share/<token>/ нет.'
        )
