"""Сигналы приложения storage (Спринт 8, задача 7).

post_save на User: каждому новому пользователю сразу создаётся UserProfile
(role viewer; superuser — admin). Superuser дополнительно привязывается к
первой существующей компании, иначе создаётся «Stream-CAD».

Обычный пользователь остаётся БЕЗ компании: привязку выполняет админ
компании через Панель управления (add_company_member). Автоматическое
«прикрепление к первой компании» удалено — на мигрированной БД первой
компанией является сидируемая «Моя компания» (миграция 0005), и каждый
новый пользователь получал доступ к чужим проектам, а Панель управления
отказывалась добавлять его в настоящую компанию («Пользователь уже
принадлежит другой компании»). Сигнал тонкий: управляемые привязки —
через attach_user_to_company() в services.py.
"""
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models.signals import post_save
from django.dispatch import receiver

from storage.domain.models import Company, UserProfile


@receiver(post_save, sender=User, dispatch_uid='storage.create_user_profile')
def create_user_profile(sender, instance, created, **kwargs):
    """Создаёт UserProfile новому пользователю.

    - UserProfile создаётся, только если его ещё нет (защита от гонок и
      повторных сигналов).
    - Роль: admin для superuser, viewer для остальных. Компания:
      superuser — первая существующая (или созданная «Stream-CAD»),
      остальные — None (привязывает админ через Панель управления).
    - kwargs['raw'] — загрузка фикстур (loaddata): не трогаем данные.

    Создание выполняется по user_id (а не user=instance): присваивание
    user=instance пиннит сигнальный инстанс профиля в кэш обратной связи
    (remote_field.set_cached_value), после чего user.userprofile при
    обновлении профиля через queryset возвращал УСТАРЕВШИЕ роль/компанию —
    на этом ломались и фикстуры тестов, и проверки прав в одном запросе.
    """
    if not created or kwargs.get('raw'):
        return

    profile = UserProfile.objects.filter(user_id=instance.pk).first()
    if profile is None:
        try:
            profile = UserProfile.objects.create(user_id=instance.pk)
        except IntegrityError:
            # Гонка: профиль уже создал параллельный поток.
            profile = UserProfile.objects.get(user_id=instance.pk)
    if not profile.role:
        profile.role = 'admin' if instance.is_superuser else 'viewer'
    if profile.company_id is None and instance.is_superuser:
        if not Company.objects.exists():
            Company.objects.create(name='Stream-CAD')
        profile.company = Company.objects.order_by('pk').first()
    profile.save()
