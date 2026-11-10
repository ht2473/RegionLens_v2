from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="IssuedDocument",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "sha256",
                    models.CharField(max_length=64, unique=True, verbose_name="SHA-256 документа"),
                ),
                ("size", models.PositiveBigIntegerField(verbose_name="размер, байт")),
                ("kind", models.CharField(max_length=32, verbose_name="вид выгрузки")),
                ("export_format", models.CharField(max_length=8, verbose_name="формат")),
                ("filename", models.CharField(max_length=255, verbose_name="имя файла")),
                (
                    "dataset_version",
                    models.CharField(
                        blank=True, max_length=32, verbose_name="версия набора данных"
                    ),
                ),
                ("signature", models.BinaryField(verbose_name="подпись CMS")),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="выдан"),
                ),
            ],
            options={
                "verbose_name": "выданный документ",
                "verbose_name_plural": "выданные документы",
                "ordering": ["-created_at"],
            },
        ),
    ]
