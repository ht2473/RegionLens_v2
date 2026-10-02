"""
Переводы справочника: без кириллицы, с числами оригинала и терминами словаря, не устаревшие.

Устаревший перевод — изменился русский оригинал: не совпал отпечаток (методика и глоссарий)
или ключ примечания (отпечаток его текста).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

from apps.content.management.commands.seed_content import GLOSSARY, METHODOLOGY
from apps.core.translation_qa import load_terms, numbers_en, numbers_ru, problems, source_hash
from apps.warehouse.etl.classify import make_checksum

pytestmark = pytest.mark.unit

REFERENCE = Path(settings.REFERENCE_DIR)
TERMS = load_terms(REFERENCE / "terms_en.json")

# Файлы названий, где ключ — русский оригинал, и их разделы.
KEYED_BY_RUSSIAN = (
    ("series_names.json", "subsections"),
    ("section_names.json", "sections"),
    ("unit_names.json", "units"),
    ("unit_names.json", "short_names"),
    ("publication_names.json", "publications"),
)


def read(name: str) -> dict[str, Any]:
    """Прочитать файл справочника."""
    return json.loads((REFERENCE / name).read_text(encoding="utf-8"))


def failures(pairs: list[tuple[str, str, str]], **options: Any) -> list[str]:
    """Замечания по парам «где — оригинал — перевод»."""
    return [
        f"{where}: {', '.join(found)} — {target[:80]}"
        for where, source, target in pairs
        for found in [problems(source, target, TERMS, **options)]
        if found
    ]


class TestChecker:
    """Сама проверка."""

    def test_grouped_numbers_are_one_number(self) -> None:
        """«10 000» и «10,000», «0,85» и «0.85» — одно число."""
        assert numbers_ru("на 10 000 человек, доля 0,85") == numbers_en("per 10,000 people, 0.85")

    def test_dates_keep_only_the_year(self) -> None:
        """Дата словами в переводе не считается пропуском чисел."""
        assert not problems("с 01.01.2017 г.", "from 1 January 2017")

    def test_missing_number_is_reported(self) -> None:
        """Потерянный год — замечание."""
        assert problems("данные с 2017 года", "data since the new method") == [
            "нет чисел оригинала: 2017"
        ]

    def test_cyrillic_and_terms_are_reported(self) -> None:
        """Кириллица в переводе и пропущенный термин словаря — замечания."""
        found = problems("Жилищный фонд", "Жилищный stock")
        assert "кириллица в переводе" in found
        found = problems("Жилищный фонд", "Dwellings", TERMS)
        assert any("жилищный фонд" in item for item in found)


class TestNameFiles:
    """Файлы названий справочника."""

    @pytest.mark.parametrize(("filename", "section"), KEYED_BY_RUSSIAN)
    def test_translations_pass(self, filename: str, section: str) -> None:
        """Каждое название переведено без кириллицы, с числами и терминами оригинала."""
        pairs = [(key, key, value) for key, value in read(filename)[section].items()]
        assert not failures(pairs), "\n".join(failures(pairs))

    def test_indicator_names_have_no_cyrillic(self) -> None:
        """Названия показателей (ключ — код, оригинал в базе) — без кириллицы."""
        names = read("indicator_names.json")["indicators"]
        pairs = [(code, "", name) for code, name in names.items()]
        assert not failures(pairs)


class TestTerritories:
    """Справочник территорий: названия, центры и примечания."""

    def test_translations_pass(self) -> None:
        """У каждого русского поля есть перевод с числами и терминами оригинала."""
        document = read("territories.json")
        items = [
            document["country"],
            *document["federal_districts"],
            *document["regions"],
            *document["aggregates"],
        ]
        pairs = [
            (f"{item['code']}.{field}", item[f"{field}_ru"], item.get(f"{field}_en", ""))
            for item in items
            for field in ("name", "capital", "note")
            if item.get(f"{field}_ru")
        ]
        assert not failures(pairs), "\n".join(failures(pairs))


class TestSourceSeries:
    """Ряды Банка России и ФНС: названия и единицы переводятся своим справочником."""

    def test_translations_pass(self) -> None:
        """Названия рядов, разделов и единиц — с числами и терминами оригинала."""
        document = read("source_series.json")
        items = [*document["series"], *document["sections"], *document["units"].values()]
        pairs = [
            (item.get("key", item.get("code", "")), item[ru], item[en])
            for item in items
            for ru, en in (("name_ru", "name_en"), ("short_ru", "short_en"))
            if item.get(ru)
        ]
        assert not failures(pairs), "\n".join(failures(pairs))

    def test_units_agree_with_unit_names(self) -> None:
        """Одна и та же единица называется по-английски одинаково в обоих справочниках."""
        names = read("unit_names.json")
        differ = [
            unit["name_ru"]
            for unit in read("source_series.json")["units"].values()
            if names["units"].get(unit["name_ru"], unit["name_en"]) != unit["name_en"]
            or names["short_names"].get(unit["short_ru"], unit["short_en"]) != unit["short_en"]
        ]
        assert not differ


class TestFeatured:
    """Подписи основного набора: краткие названия, единицы, вопросы."""

    def test_translations_pass(self) -> None:
        """Каждая подпись переведена с числами и терминами оригинала."""
        pairs = [
            (item["key"], item[f"{field}_ru"], item[f"{field}_en"])
            for item in read("featured_series.json")["series"]
            for field in ("short_title", "unit_label", "question")
            if item.get(f"{field}_ru")
        ]
        assert not failures(pairs), "\n".join(failures(pairs))


class TestNotes:
    """Переводы примечаний Росстата."""

    def test_keys_are_fingerprints_of_originals(self) -> None:
        """Ключ — отпечаток сохранённого оригинала: оригинал и ключ не разошлись."""
        notes = read("note_texts_en.json")["notes"]
        wrong = [key for key, entry in notes.items() if make_checksum(entry["ru"]) != key]
        assert not wrong

    def test_translations_pass(self) -> None:
        """Каждое примечание переведено без кириллицы, с числами и терминами оригинала."""
        notes = read("note_texts_en.json")["notes"]
        pairs = [(key[:10], entry["ru"], entry["en"]) for key, entry in notes.items()]
        assert not failures(pairs), "\n".join(failures(pairs))


class TestContent:
    """Английские версии методики и глоссария."""

    def test_versions_are_not_stale(self) -> None:
        """Отпечаток у каждой версии совпадает с нынешним русским оригиналом."""
        document = read("content_en.json")
        stale = [
            item["code"]
            for item in METHODOLOGY
            if document["MethodologySection"][item["code"]].get("source")
            != source_hash(item["title"], item["summary"], item.get("formula", ""), item["body"])
        ]
        stale += [
            item["slug"]
            for item in GLOSSARY
            if document["GlossaryTerm"][item["slug"]].get("source")
            != source_hash(
                item["term"], item["short"], item.get("definition", ""), item.get("synonyms", "")
            )
        ]
        assert not stale, f"оригинал правили после перевода: {', '.join(stale)}"

    def test_translations_pass(self) -> None:
        """Методика и глоссарий — с числами и терминами оригинала, без кириллицы."""
        document = read("content_en.json")
        pairs = []
        for item in METHODOLOGY:
            english = document["MethodologySection"][item["code"]]
            for field in ("title", "summary", "formula", "body"):
                if item.get(field):
                    pairs.append((f"{item['code']}.{field}", item[field], english[field]))
        for item in GLOSSARY:
            english = document["GlossaryTerm"][item["slug"]]
            for field, target in (
                ("term", "term"),
                ("short", "short_definition"),
                ("definition", "definition"),
            ):
                if item.get(field):
                    pairs.append((f"{item['slug']}.{target}", item[field], english[target]))
        assert not failures(pairs), "\n".join(failures(pairs))
