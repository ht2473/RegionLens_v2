from django.db import migrations, models
from django.db.models import F


def confirm_existing(apps, schema_editor):
    """Записи, заведённые до подтверждения почты, считаются подтверждёнными с даты создания."""
    user_model = apps.get_model("accounts", "User")
    user_model.objects.filter(email_confirmed_at__isnull=True).update(
        email_confirmed_at=F("created_at")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_encrypt_totp_secret"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_confirmed_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="адрес почты подтверждён"
            ),
        ),
        migrations.RunPython(confirm_existing, migrations.RunPython.noop),
    ]
