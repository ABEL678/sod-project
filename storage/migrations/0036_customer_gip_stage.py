import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def forwards_customers(apps, schema_editor):
    """Строковый Project.customer -> карточка Customer компании проекта.

    Уникальные имена внутри компании становятся карточками; пустые строки
    остаются без заказчика (customer_org=NULL).
    """
    Project = apps.get_model('storage', 'Project')
    Customer = apps.get_model('storage', 'Customer')
    for project in Project.objects.exclude(customer='').iterator():
        name = (project.customer or '').strip()
        if not name:
            continue
        customer, _ = Customer.objects.get_or_create(
            company_id=project.company_id,
            name=name,
        )
        project.customer_org = customer
        project.save(update_fields=['customer_org'])


def backwards_customers(apps, schema_editor):
    """Обратно: строка заказчика восстанавливается из карточки."""
    Project = apps.get_model('storage', 'Project')
    for project in Project.objects.select_related('customer_org').iterator():
        if project.customer_org_id:
            project.customer = project.customer_org.name
            project.save(update_fields=['customer'])


def forwards_gip(apps, schema_editor):
    """Project.gip -> состав команды (ProjectMembership role=gip).

    Правило владельца для нескольких участников с ролью «ГИП»:
    действующий — тот, кому роль присвоена последней по дате записи в составе.
    Дата записи в ProjectMembership не хранится, поэтому порядок записи
    определяется по id (монотонный суррогатный ключ).
    Если действующего установить нельзя (ни состава, ни старого поля) —
    проект не трогается и попадает в отчёт отдельным списком.
    """
    Project = apps.get_model('storage', 'Project')
    ProjectMembership = apps.get_model('storage', 'ProjectMembership')

    for project in Project.objects.all().iterator():
        gip_members = list(
            ProjectMembership.objects.filter(
                project_id=project.pk,
                role_in_project='gip',
            ).order_by('-id')
        )
        if len(gip_members) > 1:
            # Оставляем последнюю по записи, остальные снимаем с роли ГИП.
            ProjectMembership.objects.filter(
                pk__in=[m.pk for m in gip_members[1:]],
            ).update(role_in_project='developer')
            continue
        if len(gip_members) == 1:
            continue
        # Участников ГИП нет — берём старое поле, если оно задано.
        if project.gip_id:
            ProjectMembership.objects.update_or_create(
                project_id=project.pk,
                user_id=project.gip_id,
                defaults={'role_in_project': 'gip'},
            )
        # Иначе: ГИП не определён; проект без ГИП остаётся как есть (для отчёта).


def backwards_gip(apps, schema_editor):
    """Обратно: поле Project.gip заполняется текущим ГИП из состава."""
    Project = apps.get_model('storage', 'Project')
    ProjectMembership = apps.get_model('storage', 'ProjectMembership')
    for project in Project.objects.all().iterator():
        membership = (
            ProjectMembership.objects.filter(project_id=project.pk, role_in_project='gip')
            .order_by('-id')
            .first()
        )
        if membership is not None:
            project.gip_id = membership.user_id
            project.save(update_fields=['gip'])


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('storage', '0035_document_state'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Схема: карточка заказчика и связь с проектом; стадия.
        migrations.CreateModel(
            name='Customer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Наименование')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='customers', to='storage.company', verbose_name='Компания')),
            ],
            options={
                'verbose_name': 'Заказчик',
                'verbose_name_plural': 'Заказчики',
            },
        ),
        migrations.AddConstraint(
            model_name='customer',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='uniq_customer_name_per_company'),
        ),
        migrations.AddField(
            model_name='project',
            name='customer_org',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='projects', to='storage.customer', verbose_name='Заказчик'),
        ),
        migrations.AddField(
            model_name='project',
            name='stage',
            field=models.CharField(choices=[('P', 'П'), ('RD', 'РД')], default='RD', max_length=2, verbose_name='Стадия'),
        ),

        # 2. Данные: заказчики и ГИПы переносятся до удаления старых полей.
        migrations.RunPython(forwards_customers, backwards_customers),
        migrations.RunPython(forwards_gip, backwards_gip),

        # 3. Уникальность ГИП — только после того, как дубли очищены данными выше.
        migrations.AddConstraint(
            model_name='projectmembership',
            constraint=models.UniqueConstraint(condition=models.Q(('role_in_project', 'gip')), fields=('project',), name='uniq_active_gip_per_project'),
        ),

        # 4. Удаление старых строковых полей и поля ГИП.
        migrations.RemoveField(
            model_name='project',
            name='customer',
        ),
        migrations.RemoveField(
            model_name='project',
            name='gip',
        ),
    ]
