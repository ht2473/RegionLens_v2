"""
Английские названия справочника и переводы примечаний Росстата из ``data/reference``.

Сборка читает файлы, а не базу, чтобы не зависеть от её состояния.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

from .typography import fix_subsection

# Файл справочника и ключ, под которым в нём лежат названия.
SOURCES: dict[str, tuple[str, str]] = {
    "sections": ("section_names.json", "sections"),
    "indicators": ("indicator_names.json", "indicators"),
    "subsections": ("series_names.json", "subsections"),
    "units": ("unit_names.json", "units"),
    "unit_short_names": ("unit_names.json", "short_names"),
    "publications": ("publication_names.json", "publications"),
    "notes": ("note_texts_en.json", "notes"),
}


@lru_cache(maxsize=1)
def catalog_names() -> dict[str, dict[str, Any]]:
    """Прочитать английские названия справочника; кэшируется на время жизни процесса."""
    directory = Path(settings.REFERENCE_DIR)
    result: dict[str, dict[str, Any]] = {}
    for kind, (filename, key) in SOURCES.items():
        path = directory / filename
        if not path.exists():
            result[kind] = {}
            continue
        result[kind] = json.loads(path.read_text(encoding="utf-8"))[key]
    # Названия разрезов в складе — с исправленными переносами вёрстки.
    result["subsections"] = {
        fix_subsection(name): value for name, value in result["subsections"].items()
    }
    return result


def section_name_en(source_name: str) -> str | None:
    """Английское название раздела по названию источника: слаг в складе не хранится."""
    return catalog_names()["sections"].get(source_name) or None


def indicator_name_en(code: str) -> str | None:
    """Английское название показателя по его коду."""
    return catalog_names()["indicators"].get(code) or None


def subsection_name_en(name_ru: str | None) -> str | None:
    """Английское название разреза по его русскому названию."""
    if not name_ru:
        return None
    return catalog_names()["subsections"].get(name_ru) or None


def unit_name_en(name_ru: str | None) -> str | None:
    """Английское объявление единицы измерения по каноническому русскому."""
    if not name_ru:
        return None
    return catalog_names()["units"].get(name_ru) or None


def unit_short_name_en(short_name_ru: str | None) -> str | None:
    """Английское сокращение единицы измерения по русскому сокращению."""
    if not short_name_ru:
        return None
    return catalog_names()["unit_short_names"].get(short_name_ru) or None


def publication_name_en(name_ru: str | None) -> str | None:
    """Английское название статистического издания по русскому названию."""
    if not name_ru:
        return None
    return catalog_names()["publications"].get(name_ru) or None


def note_text_en(checksum: str) -> str:
    """Перевод примечания Росстата по отпечатку русского текста; нет перевода — пусто."""
    entry = catalog_names()["notes"].get(checksum)
    return str(entry["en"]) if entry else ""


def full_title_en(indicator_code: str, subsection_ru: str | None) -> str | None:
    """
    Собрать английское полное наименование ряда, если переведён сам показатель.

    Непереведённый разрез при переведённом показателе остаётся по-русски.
    """
    indicator = indicator_name_en(indicator_code)
    if not indicator:
        return None
    if not subsection_ru:
        return indicator
    return f"{indicator} — {subsection_name_en(subsection_ru) or subsection_ru}"
