"""
Начальная схема ``core``: отметки служб и функция ``rl_fold`` для поиска по русскому тексту.

``rl_fold`` приводит регистр через ICU и от локали базы не зависит. Если
PostgreSQL собран без ICU, создаётся вариант на ``lower`` — он приводит только латиницу,
но миграция не отказывает.
"""

from django.db import migrations, models

CREATE_FOLD = """
DO $create$
BEGIN
    BEGIN
        CREATE OR REPLACE FUNCTION rl_fold(value text) RETURNS text
            LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS
            $body$ SELECT replace(lower(value COLLATE "und-x-icu"), 'ё', 'е') $body$;
    EXCEPTION WHEN undefined_object THEN
        CREATE OR REPLACE FUNCTION rl_fold(value text) RETURNS text
            LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS
            $body$ SELECT replace(lower(value), 'ё', 'е') $body$;
    END;
END
$create$;
"""

DROP_FOLD = "DROP FUNCTION IF EXISTS rl_fold(text);"


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FOLD, reverse_sql=DROP_FOLD),
        migrations.CreateModel(
            name='ServiceBeat',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('service', models.CharField(choices=[('backup', 'Резервная копия'), ('mail', 'Почта'), ('collect', 'Сбор источников'), ('visits', 'Статистика посещений'), ('dataset', 'Проверка версии набора')], max_length=16, unique=True, verbose_name='служба')),
                ('seen_at', models.DateTimeField(verbose_name='отметка')),
                ('ok', models.BooleanField(default=True, verbose_name='исправна')),
                ('detail', models.JSONField(blank=True, default=dict, verbose_name='подробности')),
            ],
            options={
                'verbose_name': 'отметка службы',
                'verbose_name_plural': 'отметки служб',
            },
        ),
    ]
