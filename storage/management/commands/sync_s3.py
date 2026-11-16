"""Management-команда S3-синхронизации (sync_s3).

Использование:
  python manage.py sync_s3 --status
  python manage.py sync_s3 --ensure-bucket
  python manage.py sync_s3 --project P-25-118
  python manage.py sync_s3 --direction out
  python manage.py sync_s3 --direction in
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from storage.adapters.s3_boto3 import S3Boto3Gateway
from storage.domain.models import Project, SyncState


class Command(BaseCommand):
    help = "Управление S3-синхронизацией (Data Lake / ГИП-Ассистент)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--status', action='store_true', help='Показать состояние синхронизации',
        )
        parser.add_argument(
            '--ensure-bucket', action='store_true', help='Создать бакет, если нет',
        )
        parser.add_argument(
            '--project', type=str, default='', help='Код проекта',
        )
        parser.add_argument(
            '--direction', type=str, default='',
            choices=['', 'out', 'in'],
            help='Направление синхронизации',
        )

    def handle(self, *args, **options):
        if not settings.S3_SYNC_ENABLED:
            self.stdout.write(self.style.WARNING("S3_SYNC_ENABLED=False — синхронизация отключена"))
            if not any([options['ensure_bucket']]):
                return

        if options['ensure_bucket']:
            self._ensure_bucket()
            return

        if options['status']:
            self._show_status(options.get('project', ''))
            return

        project_code = options.get('project', '') or ''
        direction = options.get('direction', '') or ''

        if project_code:
            project = Project.objects.filter(code=project_code).first()
            if not project:
                raise CommandError(f"Проект {project_code} не найден")
            self._sync_project(project, direction)
        else:
            self.stdout.write(self.style.WARNING("Укажите --project или --status"))

    def _ensure_bucket(self):
        gateway = S3Boto3Gateway()
        try:
            gateway.ensure_bucket()
            self.stdout.write(self.style.SUCCESS(
                f"Бакет {settings.S3_BUCKET_NAME} готов",
            ))
        except Exception as exc:
            raise CommandError(f"Ошибка создания бакета: {exc}")

    def _show_status(self, project_code: str = ''):
        qs = SyncState.objects.select_related('project')
        if project_code:
            qs = qs.filter(project__code=project_code)
        rows = qs.annotate(
            log_count=Count('project__sync_logs'),
        ).order_by('project__code')
        if not rows:
            # SyncState ещё не созданы — показываем проекты без состояния
            projects = Project.objects.filter(is_deleted=False).order_by('code')
            self.stdout.write(self.style.WARNING("SyncState не найдены. Проекты без синхронизации:"))
            for p in projects:
                self.stdout.write(f"  {p.code} — {p.name}")
            return
        for row in rows:
            flag = '✓' if row.last_success_at else '✗'
            if row.dirty:
                flag = '!'
            self.stdout.write(
                f"  {row.project.code:20s} gen={row.generation:3d} "
                f"{flag} logs={row.log_count} "
                f"err={row.consecutive_failures} "
                f"ack={row.last_ack_generation}",
            )

    def _sync_project(self, project, direction: str):
        self.stdout.write(f"Синхронизация {project.code} ({direction or 'out'})")
        self.stdout.write(self.style.WARNING("export_project_to_s3 ещё не реализован"))