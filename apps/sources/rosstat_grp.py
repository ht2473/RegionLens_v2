"""
Таблица Росстата «Валовой региональный продукт по субъектам Российской Федерации»
(``VRP_s_1998.xlsx``): ВРП, ВРП на душу населения и индекс физического объёма.

Файл один и заменяется на месте раз в год (март); выпуск различается датой изменения.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from . import network
from .base import PARSED_COLUMNS, Candidate, ParsedRelease
from .sheets import SheetFormatError, open_sheets, parse_table, pick_sheet

code = "rosstat_grp"
title_ru = "Валовой региональный продукт по субъектам Российской Федерации"
title_en = "Gross regional product by region of Russia"
publisher_ru = "Росстат"
publisher_en = "Rosstat"
# Короткое имя — в подписях у значений.
short_ru = "таблица ВРП Росстата"
short_en = "Rosstat GRP table"
licence_ru = "Официальная статистическая информация, открытый доступ"
licence_en = "Official statistics, open access"
page_url = "https://rosstat.gov.ru/statistics/accounts"
check_every = timedelta(days=7)
parser_version = 2

FILE_URL = "https://rosstat.gov.ru/storage/mediabank/VRP_s_1998.xlsx"
# Итоговая строка таблицы — сумма субъектов, подписанная не «Российская Федерация».
COUNTRY_LABEL = r"^валовой региональный продукт по субъектам российской федерации \(валовая"


@dataclass(frozen=True, slots=True)
class SheetSpec:
    """Лист файла по его заголовку и показатель, который из него берётся."""

    title: str
    measure: str


# Файл делит ряды на два листа: до 2015 (индекс — до 2016) года и после.
SHEETS: tuple[SheetSpec, ...] = (
    SheetSpec(r"Валовой региональный продукт по субъектам .* в 1998-2015", "grp"),
    SheetSpec(r"Валовой региональный продукт на душу населения .* в 1998-2015", "grp_per_capita"),
    SheetSpec(
        r"Индексы физического объема валового регионального продукта в 1998-2016", "grp_index"
    ),
    SheetSpec(r"Валовой региональный продукт по субъектам .* в 2016-\d{4}", "grp"),
    SheetSpec(r"Валовой региональный продукт на душу населения .* в 2016-\d{4}", "grp_per_capita"),
    SheetSpec(
        r"Индексы физического объема валового регионального продукта в 2017-\d{4}", "grp_index"
    ),
)


def discover() -> list[Candidate]:
    """Файл на сайте; выпуск — по дате его изменения."""
    remote = network.head(FILE_URL)
    if remote.status != 200 or remote.modified is None:  # noqa: PLR2004
        return []
    day = remote.modified.date()
    return [
        Candidate(
            url=FILE_URL,
            release_code=day.isoformat(),
            title=f"по состоянию на {day:%d.%m.%Y}",
            reference_year=day.year,
            published_on=day,
            file_name="VRP_s_1998.xlsx",
        )
    ]


def identify(file_name: str, url: str = "") -> Candidate:
    """Выпуск по переданному файлу: код и дату назначит сборщик по дню получения."""
    return Candidate(
        url=url or FILE_URL, release_code="", title="", reference_year=0, file_name=file_name
    )


def parse(files: Path) -> ParsedRelease:
    """
    Разобрать ВРП, ВРП на душу населения и индекс физического объёма.

    Сноски листов («начиная с 2016 года — внедрение международной методологии…») идут
    с выпуском: по ним сборка ставит разрывы рядов, чьи значения таблица заменяет.
    """
    books = [path for path in files.rglob("*") if path.suffix.lower() in {".xlsx", ".xls"}]
    if len(books) != 1:
        raise SheetFormatError(f"ожидается одна книга ВРП, найдено {len(books)}")
    sheets = open_sheets(books[0])
    frames: list[pd.DataFrame] = []
    notes: dict[tuple[str, str], None] = {}
    report: dict[str, Any] = {"tables": {}}
    for spec in SHEETS:
        name = spec.measure if spec.measure not in report["tables"] else f"{spec.measure}_later"
        sheet, rows = pick_sheet(sheets, spec.title, by_title=True)
        table = parse_table(sheet, rows, country=COUNTRY_LABEL)
        table.check_regions()
        notes.update(dict.fromkeys((spec.measure, text) for text in table.footnotes.values()))
        records = table.records()
        regions = {item.territory_code for item in records if item.territory_code.startswith("RU-")}
        frame = pd.DataFrame(
            {
                "measure": spec.measure,
                "territory_code": [item.territory_code for item in records],
                "year": [item.year for item in records],
                "period_kind": [item.period.kind for item in records],
                "period": [item.period.number for item in records],
                "value": [item.value for item in records],
                "hidden": [item.hidden for item in records],
                "preliminary": False,
            },
            columns=list(PARSED_COLUMNS),
        )
        frames.append(frame)
        years = sorted({item.year for item in records})
        report["tables"][name] = {
            "sheet": sheet,
            "title": table.title,
            "values": int(frame["value"].notna().sum()),
            "hidden": int(frame["hidden"].sum()),
            "regions": len(regions),
            "years": [years[0], years[-1]] if years else [],
            "unmatched": table.unmatched,
        }
    return ParsedRelease(
        frame=pd.concat(frames, ignore_index=True), report=report, notes=list(notes)
    )
