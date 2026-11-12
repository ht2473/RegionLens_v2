"""
Таблица, исследование и группа регионов принадлежат учётной записи: раздел работает после
входа. Строки без владельца, оставшиеся от времени, когда раздел был открыт без входа,
удаляются; их каталоги на диске убирает очистка как осиротевшие.
"""

from django.conf import settings
from django.db import migrations, models


def drop_ownerless(apps, schema_editor):
    for name in ("Dataset", "Study", "RegionGroup"):
        apps.get_model("userdata", name).objects.filter(owner__isnull=True).delete()
    # Проверки внешних ключей в PostgreSQL отложены до конца транзакции, и изменить таблицу
    # с ожидающими проверками нельзя: выполнить их сейчас.
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("userdata", "0006_value_types"),
    ]

    operations = [
        migrations.RunPython(drop_ownerless, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="dataset", name="userdata_dataset_has_owner"),
        migrations.RemoveConstraint(model_name="study", name="userdata_study_has_owner"),
        migrations.RemoveConstraint(model_name="regiongroup", name="userdata_group_has_owner"),
        migrations.RemoveField(model_name="dataset", name="guest_key"),
        migrations.RemoveField(model_name="dataset", name="expires_at"),
        migrations.RemoveField(model_name="study", name="guest_key"),
        migrations.RemoveField(model_name="study", name="expires_at"),
        migrations.RemoveField(model_name="regiongroup", name="guest_key"),
        migrations.RemoveField(model_name="regiongroup", name="expires_at"),
        migrations.AlterField(
            model_name="dataset",
            name="owner",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="datasets",
                to=settings.AUTH_USER_MODEL,
                verbose_name="владелец",
            ),
        ),
        migrations.AlterField(
            model_name="study",
            name="owner",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="studies",
                to=settings.AUTH_USER_MODEL,
                verbose_name="владелец",
            ),
        ),
        migrations.AlterField(
            model_name="regiongroup",
            name="owner",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="region_groups",
                to=settings.AUTH_USER_MODEL,
                verbose_name="владелец",
            ),
        ),
    ]
