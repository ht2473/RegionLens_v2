"""
Загрузка английских названий справочника и переводов методических примечаний Росстата.

Переводы лежат файлами в ``data/reference``; запись без перевода показывается по-русски.
"""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import (
    Indicator,
    MethodologyNote,
    Publication,
    Section,
    Series,
    Unit,
)

# Файлы справочника и разделы, которые из каждого читаются.
SOURCES = {
    "section_names.json": ("sections",),
    "indicator_names.json": ("indicators",),
    "series_names.json": ("subsections",),
    "unit_names.json": ("units", "short_names"),
    "publication_names.json": ("publications",),
    "note_texts_en.json": ("notes",),
}


class Command(BaseCommand):
    """Проставить английские названия разделов, показателей и разрезов."""

    help = "Загружает английские названия справочника из data/reference/*_names.json"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument(
            "--check",
            action="store_true",
            help="Только сверить состав, не изменяя базу данных",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Точка входа команды."""
        directory = Path(settings.REFERENCE_DIR)
        payloads: dict[str, dict[str, Any]] = {}
        for filename, keys in SOURCES.items():
            path = directory / filename
            if not path.exists():
                raise CommandError(f"Файл названий не найден: {path}")
            document = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                payloads[key] = document[key]

        check = bool(options["check"])
        self._apply_sections(payloads["sections"], check=check)
        self._apply_indicators(payloads["indicators"], check=check)
        self._apply_subsections(payloads["subsections"], check=check)
        self._apply_units(payloads["units"], payloads["short_names"], check=check)
        self._apply_publications(payloads["publications"], check=check)
        self._apply_notes(payloads["notes"], check=check)

        if not check:
            # Перечень рядов кэшируется по языкам; сброс — чтобы показать новые переводы.
            for language, _name in settings.LANGUAGES:
                cache.delete(f"catalog:series-options:{language}")
            self.stdout.write("Кэш перечня рядов сброшен")

    def _apply_sections(self, names: dict[str, str], *, check: bool) -> None:
        """
        Проставить названия разделов, сопоставляя по названию источника.

        Слаг не годится: при совпадении названий он получает суффикс и в складе не хранится.
        """
        records = {section.source_name: section for section in Section.objects.all()}
        self._update(records, names, "разделов", check=check)

    def _apply_indicators(self, names: dict[str, str], *, check: bool) -> None:
        """Проставить названия показателей, сопоставляя по коду набора данных."""
        records = {indicator.code: indicator for indicator in Indicator.objects.all()}
        self._update(records, names, "показателей", check=check)

    def _apply_subsections(self, names: dict[str, str], *, check: bool) -> None:
        """
        Проставить названия разрезов, сопоставляя по русскому названию.

        Один разрез («Всего») встречается у многих показателей и переводится одинаково.
        """
        changed = []
        untranslated = set()
        for series in Series.objects.filter(has_subsection=True).exclude(name_ru=""):
            translation = names.get(series.name_ru, "")
            if not translation:
                untranslated.add(series.name_ru)
            elif series.name_en != translation:
                series.name_en = translation
                changed.append(series)

        if untranslated:
            self.stdout.write(
                self.style.WARNING(f"останутся без перевода (разрезов): {len(untranslated)}")
            )
        self.stdout.write(f"разрезов к обновлению: {len(changed)}")
        if not check:
            Series.objects.bulk_update(changed, ["name_en"], batch_size=200)
            self.stdout.write(self.style.SUCCESS(f"Обновлено разрезов: {len(changed)}"))

    def _apply_publications(self, names: dict[str, str], *, check: bool) -> None:
        """Проставить английские названия статистических изданий."""
        records = {publication.name_ru: publication for publication in Publication.objects.all()}
        self._update(records, names, "изданий", check=check)

    def _apply_units(
        self,
        names: dict[str, str],
        short_names: dict[str, str],
        *,
        check: bool,
    ) -> None:
        """
        Проставить английские названия и сокращения единиц измерения.

        Ключ — канонический русский текст: у десятка объявлений источника он один.
        """
        untranslated = Unit.objects.exclude(name_ru__in=names).filter(name_en="").count()
        if untranslated:
            self.stdout.write(
                self.style.WARNING(f"останутся без перевода (единиц измерения): {untranslated}")
            )
        changed = []
        for unit in Unit.objects.all():
            name_en = names.get(unit.name_ru, "")
            short_en = short_names.get(unit.short_name_ru, "")
            if (name_en and unit.name_en != name_en) or (
                short_en and unit.short_name_en != short_en
            ):
                unit.name_en = name_en or unit.name_en
                unit.short_name_en = short_en or unit.short_name_en
                changed.append(unit)

        self.stdout.write(f"единиц измерения к обновлению: {len(changed)}")
        if check or not changed:
            return

        Unit.objects.bulk_update(changed, ["name_en", "short_name_en"], batch_size=200)
        self.stdout.write(self.style.SUCCESS(f"Обновлено единиц измерения: {len(changed)}"))

    def _apply_notes(self, notes: dict[str, dict[str, str]], *, check: bool) -> None:
        """
        Проставить переводы примечаний Росстата, сопоставляя по отпечатку русского текста.

        Изменённое источником примечание получает другой отпечаток и остаётся без перевода,
        пока его не переведут заново. Полнота сверяется по примечаниям, которые показываются
        читателю: влияющим на сопоставимость.
        """
        changed = []
        for note in MethodologyNote.objects.all():
            entry = notes.get(note.checksum)
            if entry and note.text_en != entry["en"]:
                note.text_en = entry["en"]
                changed.append(note)

        shown = MethodologyNote.objects.filter(affects_comparability=True)
        untranslated = shown.exclude(checksum__in=notes).count()
        if untranslated:
            self.stdout.write(
                self.style.WARNING(f"останутся без перевода (примечаний): {untranslated}")
            )
        self.stdout.write(f"примечаний к обновлению: {len(changed)}")
        if check or not changed:
            return
        MethodologyNote.objects.bulk_update(changed, ["text_en"], batch_size=200)
        self.stdout.write(self.style.SUCCESS(f"Обновлено примечаний: {len(changed)}"))

    def _update(
        self,
        records: dict[str, Any],
        names: dict[str, str],
        title: str,
        *,
        check: bool,
    ) -> None:
        """Сверить и записать английские названия для одного справочника."""
        unknown = sorted(set(names) - set(records))
        # Ряды внешних источников получают английские названия из своего справочника.
        untranslated = sorted(
            key for key, record in records.items() if key not in names and not record.name_en
        )

        if unknown:
            self.stdout.write(self.style.WARNING(f"нет в каталоге ({title}): {len(unknown)}"))
        if untranslated:
            self.stdout.write(
                self.style.WARNING(f"останутся без перевода ({title}): {len(untranslated)}")
            )

        changed = []
        for key, name_en in names.items():
            record = records.get(key)
            if record is not None and record.name_en != name_en:
                record.name_en = name_en
                changed.append(record)

        self.stdout.write(f"{title} к обновлению: {len(changed)}")
        if check or not changed:
            return

        model = type(changed[0])
        model.objects.bulk_update(changed, ["name_en"], batch_size=200)
        self.stdout.write(self.style.SUCCESS(f"Обновлено {title}: {len(changed)}"))
