"""Сигналы приложения storage (Спринт 8, задача 7).

post_save на User: каждому новому пользователю сразу создаётся UserProfile.
Superuser дополнительно привязывается к компании (первой существующей,
иначе создаётся «Stream-CAD»); обычный пользователь получает роль viewer
и привязывается к первой компании, если она есть. Сигнал тонкий:
управляемые привязки — через attach_user_to_company() в services.py.
"""
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from storage.domain.models import Company, UserProfile


@receiver(post_save, sender=User, dispatch_uid='storage.create_user_profile')
def create_user_profile(sender, instance, created, **kwargs):
    """Создаёт UserProfile новому пользователю и привязывает к компании.

    - UserProfile создаётся, только если его ещё нет (get_or_create —
      защита от гонок и повторных сигналов).
    - superuser без компаний получает компанию «Stream-CAD».
    - Профиль привязывается к первой компании, если она есть.
    - Роль: admin для superuser, viewer для остальных (админ может
      переназначить через Панель управления).
    - kwargs['raw'] — загрузка фикстур (loaddata): не трогаем данные.
    """
    if not created or kwargs.get('raw'):
        return

    profile, _ = UserProfile.objects.get_or_create(user=instance)
    if not profile.role:
        profile.role = 'admin' if instance.is_superuser else 'viewer'
    if profile.company_id is None:
        if instance.is_superuser and not Company.objects.exists():
            Company.objects.create(name='Stream-CAD')
        profile.company = Company.objects.first()
    profile.save()
