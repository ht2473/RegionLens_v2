"""Доска становится исследованием: переименование, гостевые исследования, снимок для «Вернуть»."""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("userdata", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="share", name="userdata_share_one_target"),
        migrations.RenameModel(old_name="Board", new_name="Study"),
        migrations.RenameField(model_name="share", old_name="board", new_name="study"),
        migrations.AlterModelOptions(
            name="study",
            options={
                "ordering": ["-updated_at"],
                "verbose_name": "исследование",
                "verbose_name_plural": "исследования",
            },
        ),
        migrations.AlterField(
            model_name="study",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="studies",
                to=settings.AUTH_USER_MODEL,
                verbose_name="владелец",
            ),
        ),
        migrations.AddField(
            model_name="study",
            name="guest_key",
            field=models.CharField(
                blank=True, db_index=True, max_length=64, verbose_name="отпечаток гостевого ключа"
            ),
        ),
        migrations.AddField(
            model_name="study",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="хранится до"),
        ),
        migrations.AddField(
            model_name="study",
            name="undo",
            field=models.JSONField(blank=True, default=list, verbose_name="блоки до правки"),
        ),
        migrations.AlterField(
            model_name="share",
            name="study",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="shares",
                to="userdata.study",
                verbose_name="исследование",
            ),
        ),
        migrations.AddConstraint(
            model_name="study",
            constraint=models.CheckConstraint(
                condition=models.Q(owner__isnull=False) | ~models.Q(guest_key=""),
                name="userdata_study_has_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="share",
            constraint=models.CheckConstraint(
                condition=models.Q(dataset__isnull=False, study__isnull=True)
                | models.Q(dataset__isnull=True, study__isnull=False),
                name="userdata_share_one_target",
            ),
        ),
    ]
