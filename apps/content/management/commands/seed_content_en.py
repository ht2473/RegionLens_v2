"""
Загрузка английских версий разделов методики и терминов глоссария из ``content_en.json``.

Запись без перевода показывается по-русски с оговоркой. У каждой версии — отпечаток русского
оригинала: не совпал — оригинал правили после перевода, и версия названа устаревшей.
"""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction

from apps.content.models import GlossaryTerm, MethodologySection
from apps.core.translation_qa import source_hash

DEFAULT_PATH = "content_en.json"

# Модель, поле её устойчивого идентификатора и переводимые поля.
TARGETS: tuple[tuple[type[models.Model], str, tuple[str, ...]], ...] = (
    (MethodologySection, "code", ("title", "summary", "formula", "body")),
    (GlossaryTerm, "slug", ("term", "short_definition", "definition", "synonyms")),
)


class Command(BaseCommand):
    """Проставить английские версии разделов методики и терминов глоссария."""

    help = "Загружает английские версии содержимого из data/reference/content_en.json"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument(
            "--check",
            action="store_true",
            help="Только сверить состав, не изменяя базу данных",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Точка входа команды."""
        path = Path(settings.REFERENCE_DIR) / DEFAULT_PATH
        if not path.exists():
            raise CommandError(f"Файл с английскими версиями не найден: {path}")

        payload = json.loads(path.read_text(encoding="utf-8"))
        check = bool(options["check"])

        with transaction.atomic():
            for model, key_field, fields in TARGETS:
                self._apply(model, key_field, fields, payload.get(model.__name__, {}), check=check)
            if check:
                transaction.set_rollback(True)

    def _apply(
        self,
        model: type[models.Model],
        key_field: str,
        fields: tuple[str, ...],
        translations: dict[str, dict[str, str]],
        *,
        check: bool,
    ) -> None:
        """Записать английскую версию для одного вида содержимого."""
        # Менеджер объявлен в модели, и через тип модели mypy его не видит.
        manager: Any = model._default_manager
        records = {getattr(obj, key_field): obj for obj in manager.all()}

        unknown = sorted(set(translations) - set(records))
        untranslated = sorted(set(records) - set(translations))
        if unknown:
            self.stdout.write(
                self.style.WARNING(f"нет таких записей ({model.__name__}): {', '.join(unknown)}")
            )
        if untranslated:
            self.stdout.write(
                self.style.WARNING(
                    f"останутся без английской версии ({model.__name__}): {len(untranslated)}"
                )
            )

        stale = []
        written = 0
        for key, values in translations.items():
            record = records.get(key)
            if record is None:
                continue
            record.set_current_language("ru")
            original = source_hash(*(str(getattr(record, field) or "") for field in fields))
            if values.get("source") != original:
                stale.append(key)
            record.set_current_language("en")
            for field in fields:
                setattr(record, field, values.get(field, ""))
            record.save()
            written += 1

        if stale:
            self.stdout.write(
                self.style.WARNING(
                    f"устарели после правки оригинала ({model.__name__}): {', '.join(stale)}"
                )
            )
        action = "сверено" if check else "записано"
        self.stdout.write(self.style.SUCCESS(f"{model.__name__}: {action} версий — {written}"))
