"""Аудит 6.0: бэкфилл профилей и компаний для существующих пользователей.

Сигнал create_user_profile покрывает только НОВЫХ пользователей
(created=True). Пользователи, созданные до подключения сигнала
(например, abel), оставались без UserProfile: get_user_company -> None,
get_accessible_* -> .none() — fail-closed, но это потеря доступа, а
шаблоны вида {% if user.userprofile.role == 'admin' %} молча давали False.

Команда безопасна к повторному запуску (get_or_create / фильтр по уже
существующим профилям) и ничего не удаляет.

Запуск: python manage.py backfill_profiles [--company ID] [--dry-run]
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from storage.domain.models import Company, UserProfile
from storage.domain.services import _clear_profile_cache


class Command(BaseCommand):
    help = (
        'Создаёт UserProfile пользователям без профиля и опционально '
        'привязывает их к компании (роли не перезаписывает).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--company',
            type=int,
            default=None,
            metavar='ID',
            help='pk компании для привязки (по умолчанию — без привязки).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать план действий, ничего не меняя.',
        )

    def handle(self, *args, **options):
        company = None
        company_id = options.get('company')
        if company_id is not None:
            company = Company.objects.filter(pk=company_id).first()
            if company is None:
                raise CommandError(
                    f'Компания с pk={company_id} не найдена.'
                )

        users = User.objects.all().order_by('pk')
        created_profiles = 0
        attached = 0
        skipped = 0

        for user in users.iterator():
            profile = UserProfile.objects.filter(user=user).first()
            if profile is None:
                role = 'admin' if user.is_superuser else 'viewer'
                if options.get('dry_run'):
                    self.stdout.write(
                        f'[dry-run] создал бы профиль: {user.username} '
                        f'(роль {role})'
                    )
                else:
                    profile = UserProfile.objects.create(
                        user=user, role=role,
                    )
                    _clear_profile_cache(user)
                created_profiles += 1
            else:
                profile = UserProfile.objects.select_related('user').get(
                    user=user
                )

            if company is not None and (
                profile is None or profile.company_id is None
            ):
                if options.get('dry_run'):
                    self.stdout.write(
                        f'[dry-run] привязал бы: {user.username} -> '
                        f'«{company.name}»'
                    )
                else:
                    profile.company = company
                    profile.save(update_fields=['company'])
                    _clear_profile_cache(user)
                attached += 1
            elif company is not None and profile is not None \
                    and profile.company_id != company.pk:
                skipped += 1
                self.stdout.write(
                    f'Пропущен {user.username}: уже в другой компании '
                    f'(pk={profile.company_id}).'
                )

        self.stdout.write(self.style.SUCCESS(
            f'Готово. Профилей создано: {created_profiles}; '
            f'привязано к компании: {attached}; пропущено: {skipped}.'
        ))
