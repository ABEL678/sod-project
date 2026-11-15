"""Команда синхронизации разделов: перенос папок/документов/чанков
от COMMON к реальным Section по шаблону проекта.

Запуск:
    python manage.py sync_project_sections
    python manage.py sync_project_sections --project 1
    python manage.py sync_project_sections --project 1 --dry-run
    python manage.py sync_project_sections --default-marks RD

Без флага --project обрабатывает все активные проекты.
--dry-run печатает план, ничего не меняя.
--default-marks задаёт шаблон для проектов без template_key (RD или PD_87).
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from ai_checks.domain.models import ProjectDocumentChunk
from storage.domain.models import Document, Folder, Project, Section
from storage.domain.project_templates import (
    PROJECT_TEMPLATES,
    PD_SECTIONS,
    RD_SECTIONS,
)
from storage.domain.services import (
    _ensure_project_sections,
    _get_or_create_common_section,
    generate_gost_code,
)

User = get_user_model()


def _normalize_name(name: str) -> str:
    """Нормализация имени папки для сопоставления с шаблоном."""
    return ' '.join(name.split()).strip().lower()


def _build_mark_map(template_key: str) -> dict[str, str]:
    """Строит карту {нормализованное_имя_папки: код_марки}.

    Возвращает словарь, где ключ — нормализованное имя папки из шаблона,
    значение — код марки (код Section).
    """
    template = PROJECT_TEMPLATES.get(template_key, {})
    mark_map: dict[str, str] = {}

    if template.get('pd'):
        for sd in PD_SECTIONS:
            # Имя папки раздела (полное) и имя "Часть 1"
            mark_map[_normalize_name(sd['name'])] = sd['mark']
            if 'part' in sd:
                mark_map[_normalize_name(sd['part'])] = sd['mark']
            for sub in sd.get('subsections', ()):
                mark_map[_normalize_name(sub['name'])] = sd['mark']
                if 'part' in sub:
                    mark_map[_normalize_name(sub['part'])] = sd['mark']

    if template.get('rd'):
        for sd in RD_SECTIONS:
            # РД-секции имеют только 'mark' и 'name', без 'part'
            mark_map[_normalize_name(sd['name'])] = sd['mark']

    return mark_map


class Command(BaseCommand):
    help = (
        'Синхронизирует разделы проекта: создаёт Section по шаблону, '
        'перепривязывает папки/документы/чанки от COMMON к маркам.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--project',
            type=int,
            default=None,
            help='pk проекта (без флага — все активные).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Печатает план, ничего не меняя.',
        )
        parser.add_argument(
            '--default-marks',
            type=str,
            default=None,
            choices=['RD', 'PD_87'],
            help='Шаблон для проектов без template_key.',
        )

    def handle(self, *args, **options):
        project_filter = options.get('project')
        dry_run = options.get('dry_run')
        default_marks = options.get('default_marks')

        if project_filter is not None:
            projects = Project.objects.filter(pk=project_filter, is_deleted=False)
        else:
            projects = Project.objects.filter(is_deleted=False)

        if not projects.exists():
            self.stdout.write('Проекты не найдены.')
            return

        for project in projects.iterator():
            self.stdout.write(
                f'{'[dry-run] ' if dry_run else ''}Проект: {project.name} '
                f'(pk={project.pk}, template_key={project.template_key!r})'
            )
            self._sync_project(project, default_marks, dry_run)

    def _sync_project(self, project: Project, default_marks: str | None, dry_run: bool):
        """Синхронизация одного проекта (транзакция)."""
        from django.db import transaction

        template_key = project.template_key or default_marks
        if template_key is None:
            self.stdout.write(
                f'  Пропущен: нет template_key и --default-marks.'
            )
            return

        if template_key not in PROJECT_TEMPLATES:
            self.stdout.write(
                f'  Пропущен: неизвестный шаблон {template_key!r}.'
            )
            return

        template = PROJECT_TEMPLATES[template_key]

        with transaction.atomic():
            # 1. Создаём недостающие разделы
            sections = _ensure_project_sections(project, None, template)
            self.stdout.write(
                f'  Разделы: {len(sections)} (создано/уже существует)'
            )
            for code, sd in sections.items():
                self.stdout.write(
                    f'    + {code}: {sd.name}'
                )

            # 2. Перепривязка папок
            common = _get_or_create_common_section(project)
            mark_map = _build_mark_map(template_key)

            # Собираем все папки проекта
            all_folders = list(
                project.folders.filter(is_deleted=False).select_related('parent')
            )
            folder_by_id = {f.pk: f for f in all_folders}

            # Определяем, какие папки привязать
            # Для каждой папки ищем её имя в mark_map
            folder_rebinds: dict[int, str] = {}  # folder_pk -> mark_code
            unmatched_names: list[str] = []

            for folder in all_folders:
                if folder.section_id == common.pk:
                    # Проверяем, является ли эта папка папкой марки
                    norm = _normalize_name(folder.name)
                    if norm in mark_map:
                        folder_rebinds[folder.pk] = mark_map[norm]
                    else:
                        # Проверяем, является ли дочерней папкой привязанной марки
                        parent_mark = self._find_parent_mark(
                            folder, folder_by_id, folder_rebinds,
                        )
                        if parent_mark:
                            folder_rebinds[folder.pk] = parent_mark
                        else:
                            unmatched_names.append(folder.name)

            # Применяем перепривязку
            rebinded_count = 0
            for folder_pk, mark_code in folder_rebinds.items():
                section_obj = sections.get(mark_code)
                if section_obj is None:
                    continue
                if dry_run:
                    self.stdout.write(
                        f'  [dry-run] перепривязал папку '
                        f'«{folder_by_id[folder_pk].name}» -> {mark_code}'
                    )
                else:
                    folder_by_id[folder_pk].section = section_obj
                    folder_by_id[folder_pk].save(update_fields=['section_id'])
                rebinded_count += 1

            self.stdout.write(
                f'  Перепривязано папок: {rebinded_count}'
            )
            if unmatched_names:
                self.stdout.write(
                    f'  Не сопоставлено папок ({len(unmatched_names)}): '
                    f'{", ".join(unmatched_names[:10])}'
                )

            # 3. Перенос документов
            doc_count = 0
            if folder_rebinds:
                rebinded_folders = set(folder_rebinds.keys())
                for mark_code, section_obj in sections.items():
                    # Находим все папки этой марки (ребинданные + наследники)
                    mark_folders = self._get_mark_folders(
                        rebinded_folders, folder_by_id, mark_code,
                    )
                    if mark_folders:
                        doc_count += Document.objects.filter(
                            folder__in=mark_folders,
                            is_deleted=False,
                        ).update(section=section_obj)

            self.stdout.write(
                f'  Перенесено документов: {doc_count}'
            )

            # 4. Обновляем RAG-чанки
            chunk_count = 0
            if folder_rebinds:
                rebinded_folders = set(folder_rebinds.keys())
                for mark_code, section_obj in sections.items():
                    mark_folders = self._get_mark_folders(
                        rebinded_folders, folder_by_id, mark_code,
                    )
                    if mark_folders:
                        chunk_count += ProjectDocumentChunk.objects.filter(
                            document__folder__in=mark_folders,
                        ).update(section=section_obj)

            self.stdout.write(
                f'  Обновлено чанков: {chunk_count}'
            )

            # 5. Пересчёт шифров
            if not dry_run:
                self._refresh_project_full_codes(project)

            self.stdout.write(
                self.style.SUCCESS(
                    f'Готово. Разделы: {len(sections)}, '
                    f'папок: {rebinded_count}, '
                    f'документов: {doc_count}, '
                    f'чанков: {chunk_count}.'
                )
            )

    def _find_parent_mark(
        self,
        folder,
        folder_by_id: dict,
        folder_rebinds: dict,
    ) -> str | None:
        """Ищет код марки у ближайшего ребинданного родителя."""
        current = folder.parent
        while current is not None:
            if current.pk in folder_rebinds:
                return folder_rebinds[current.pk]
            current = current.parent
        return None

    def _get_mark_folders(
        self,
        rebinded_pks: set,
        folder_by_id: dict,
        mark_code: str,
    ) -> list:
        """Собирает все папки марки (ребинданные + потомки)."""
        mark_folders = []
        root_pks = {
            pk for pk in rebinded_pks
            if folder_by_id[pk].parent_id not in rebinded_pks
        }
        for root_pk in root_pks:
            mark_folders.extend(
                self._collect_descendants(root_pk, folder_by_id)
            )
        return mark_folders

    def _collect_descendants(
        self, root_pk: int, folder_by_id: dict,
    ) -> list:
        """Рекурсивно собирает потомков папки."""
        result = [folder_by_id[root_pk]]
        for child in folder_by_id.values():
            if child.parent_id == root_pk:
                result.extend(self._collect_descendants(child.pk, folder_by_id))
        return result

    def _refresh_project_full_codes(self, project: Project):
        """Пересчитывает full_code для разделов и папок проекта."""
        for section in project.sections.all():
            section.full_code = generate_gost_code(
                project.base_code, section.code,
            )
            section.save(update_fields=['full_code'])

        def refresh(folder) -> None:
            old_code = (folder.code or '').strip()
            if old_code and project.base_code and old_code.startswith(
                f'{project.base_code}-'
            ):
                folder.code = (
                    f'{project.base_code}{old_code[len(project.base_code):]}'
                )
                folder.full_code = folder.code
                folder.save(update_fields=['code', 'full_code'])
            else:
                folder.full_code = self._folder_full_code(
                    project,
                    folder.section,
                    folder.parent,
                    folder.code,
                )
                folder.save(update_fields=['full_code'])
            for child in Folder.objects.filter(parent=folder, is_deleted=False):
                refresh(child)

        from storage.domain.models import Folder

        for root in Folder.objects.filter(
            project=project, parent__isnull=True, is_deleted=False,
        ):
            if root.is_system_zone:
                continue
            refresh(root)

    def _folder_full_code(
        self, project, section, parent, code,
    ) -> str:
        """Собирает full_code папки из пути."""
        parts = []
        if section:
            parts.append(section.code)
        # Добавляем коды всех родителей (кроме root)
        current = parent
        while current is not None:
            if current.code:
                parts.insert(0, current.code)
            current = current.parent
        if code:
            parts.append(code)
        return '-'.join(p for p in parts if p)
