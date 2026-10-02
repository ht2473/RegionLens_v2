"""
Ежемесячный бюллетень Росстата «Информация для ведения мониторинга социально-экономического
положения субъектов Российской Федерации».

Каждая таблица несёт все месяцы с января 2016 года, поэтому для данных хватает последнего
выпуска; прежние выпуски хранят прежние редакции значений. Разбираются только таблицы,
которые продолжают ряды склада (``data/reference/source_links.json``).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from . import network
from .base import PARSED_COLUMNS, Candidate, ParsedRelease
from .sheets import SheetFormatError, open_sheets, parse_cell, parse_table, pick_sheet
from .territories import region_codes, territory_code

code = "rosstat_bulletin"
title_ru = (
    "Информация для ведения мониторинга социально-экономического положения "
    "субъектов Российской Федерации"
)
title_en = "Information for monitoring the socio-economic situation of the regions of Russia"
publisher_ru = "Росстат"
publisher_en = "Rosstat"
# Короткое имя — в подписях у значений.
short_ru = "бюллетень Росстата"
short_en = "Rosstat bulletin"
licence_ru = "Официальная статистическая информация, открытый доступ"
licence_en = "Official statistics, open access"
page_url = "https://rosstat.gov.ru/folder/11109/document/13259"
check_every = timedelta(days=1)
parser_version = 2

FILE_URL = "https://rosstat.gov.ru/storage/mediabank/info-stat-{month:02d}-{year}.{ext}"
_FILE_NAME = re.compile(r"info-stat-(\d{2})-(\d{4})\.(zip|rar)", re.IGNORECASE)
_PAGE_ITEM = re.compile(
    r'href="(?P<href>[^"]*info-stat-\d{2}-\d{4}\.(?:zip|rar))"(?P<tail>.{0,1500}?)'
    r"(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{4})",
    re.IGNORECASE | re.DOTALL,
)
_TABLE_CODE = re.compile(r"^(\d{2}-\d{2})\b")
# Сноска к году: «предварительные данные», «оценка», «вторая оценка»; третья оценка —
# окончательные данные по регламенту Росстата.
_PRELIMINARY = re.compile(r"предварительн|(?<!\w)оценк", re.IGNORECASE)
_FINAL = re.compile(r"треть\w*\s+оценк|окончательн", re.IGNORECASE)
_YEAR_RANGE = re.compile(r"(\d{4})\s*[-–—]\s*(\d{4})")
_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")

MONTH_NAMES = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Таблица бюллетеня и показатель, который из неё берётся."""

    table: str  # номер таблицы в имени файла: «12-01»
    sheet: str  # образец названия листа
    measure: str
    header: str | None = None  # образец группы столбцов в шапке
    country_unit: tuple[str, float] | None = None  # укрупнённая единица строки страны
    absent: frozenset[str] = frozenset()  # субъекты, которых в таблице нет у источника


TABLES: tuple[TableSpec, ...] = (
    TableSpec("01-01", r"к соотв\.?\s*период", "industry_index"),
    TableSpec("03-02", r"ввод жилья с уч[её]том", "housing", country_unit=("млн", 1000.0)),
    TableSpec("05-01", r"^млн", "retail", country_unit=("млрд", 1000.0)),
    TableSpec("05-01", r"к соотв\.?\s*период", "retail_index"),
    TableSpec("07-01", r"^млн", "investment"),
    TableSpec("07-01", r"к соотв\.?\s*период", "investment_index"),
    TableSpec("08-04", r"%", "unprofitable_share"),
    TableSpec("09-01", r"к декабрю", "cpi_december"),
    # Стоимость набора — по областям вместе с автономными округами.
    TableSpec("09-06", r"конец периода", "basket", absent=frozenset({"RU-ARK", "RU-TYU"})),
    TableSpec("10-05", r"первичн", "price_primary"),
    TableSpec("10-05", r"вторичн", "price_secondary"),
    TableSpec("11-01", r"^рубл", "income"),
    TableSpec("11-02", r"к соотв\.?\s*период", "real_income"),
    TableSpec("12-01", r"^рубл", "wage"),
    TableSpec("13-01", r"^\d{4}\s*-\s*\d{4}", "unemployment_rate", header=r"уровень безработицы"),
)
# Второй лист таблицы 13-01 — последние три месяца в среднем: одна скользящая тройка
# месяцев на выпуск, история складывается из выпусков.
RECENT_UNEMPLOYMENT = ("13-01", "unemployment_3m")
_MONTH_STEMS = (
    ("январ", 1),
    ("феврал", 2),
    ("март", 3),
    ("апрел", 4),
    ("ма", 5),
    ("июн", 6),
    ("июл", 7),
    ("август", 8),
    ("сентябр", 9),
    ("октябр", 10),
    ("ноябр", 11),
    ("декабр", 12),
)
_MONTH_WORD = re.compile(
    r"(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*",
    re.IGNORECASE,
)


def discover() -> list[Candidate]:
    """Выпуски со страницы бюллетеня; страница недоступна — проверка адреса следующего месяца."""
    try:
        text = network.page(page_url)
    except network.FetchError:
        return []
    found: dict[str, Candidate] = {}
    for match in _PAGE_ITEM.finditer(text):
        href = html.unescape(match.group("href"))
        url = href if href.startswith("http") else f"https://rosstat.gov.ru{href}"
        candidate = identify(url.rsplit("/", 1)[-1], url)
        published = date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
        found.setdefault(
            candidate.release_code,
            Candidate(
                url=url,
                release_code=candidate.release_code,
                title=candidate.title,
                reference_year=candidate.reference_year,
                published_on=published,
                file_name=candidate.file_name,
            ),
        )
    return sorted(found.values(), key=_order, reverse=True)


def next_candidates(after: str) -> list[Candidate]:
    """Адреса выпуска за следующий месяц после ``after`` (``ММ-ГГГГ``) в обоих форматах."""
    month, year = (int(part) for part in after.split("-"))
    month, year = (1, year + 1) if month == 12 else (month + 1, year)  # noqa: PLR2004
    return [
        identify(
            f"info-stat-{month:02d}-{year}.{ext}", FILE_URL.format(month=month, year=year, ext=ext)
        )
        for ext in ("zip", "rar")
    ]


def identify(file_name: str, url: str = "") -> Candidate:
    """Выпуск по имени файла ``info-stat-ММ-ГГГГ.zip``."""
    match = _FILE_NAME.search(file_name)
    if match is None:
        raise ValueError(f"имя «{file_name}» не похоже на выпуск бюллетеня (info-stat-ММ-ГГГГ)")
    month, year = int(match.group(1)), int(match.group(2))
    period = MONTH_NAMES[0] if month == 1 else f"{MONTH_NAMES[0]} — {MONTH_NAMES[month - 1]}"
    return Candidate(
        url=url,
        release_code=f"{month:02d}-{year}",
        title=f"{period} {year} г.",
        reference_year=year,
        file_name=match.group(0),
    )


def parse(files: Path) -> ParsedRelease:
    """Разобрать таблицы, нужные рядам склада."""
    tables = _index_tables(files)
    frames: list[pd.DataFrame] = []
    report: dict[str, Any] = {"tables": {}}
    books: dict[Path, dict[str, list[list[Any]]]] = {}

    for spec in TABLES:
        path = tables.get(spec.table)
        if path is None:
            raise SheetFormatError(f"в выпуске нет таблицы {spec.table}")
        if path not in books:
            books[path] = open_sheets(path)
        try:
            sheet, rows = pick_sheet(books[path], spec.sheet)
            table = parse_table(sheet, rows)
            table.check_regions(absent=spec.absent)
            records = table.records(header=spec.header, country_unit=spec.country_unit)
        except SheetFormatError as error:
            raise SheetFormatError(f"таблица {spec.table} ({path.name}): {error}") from error

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
                "preliminary": [
                    is_preliminary(table.note_text(item.notes), item.year) for item in records
                ],
            },
            columns=list(PARSED_COLUMNS),
        )
        frames.append(frame)
        years = sorted({item.year for item in records})
        report["tables"][spec.measure] = {
            "table": spec.table,
            "file": path.name,
            "sheet": sheet,
            "title": table.title,
            "values": int(frame["value"].notna().sum()),
            "hidden": int(frame["hidden"].sum()),
            "regions": len(regions),
            "years": [years[0], years[-1]] if years else [],
            "preliminary_years": sorted(
                {int(year) for year in frame.loc[frame["preliminary"], "year"].unique()}
            ),
            "unmatched": table.unmatched,
        }

    table_code, measure = RECENT_UNEMPLOYMENT
    path = tables[table_code]
    frame, details = recent_unemployment(books.get(path) or open_sheets(path), measure)
    frames.append(frame)
    report["tables"][measure] = {"table": table_code, "file": path.name, **details}
    return ParsedRelease(frame=pd.concat(frames, ignore_index=True), report=report)


def recent_unemployment(
    sheets: dict[str, list[list[Any]]], measure: str
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Уровень безработицы в среднем за последние три месяца со второго листа таблицы 13-01.

    Период записывается последним месяцем тройки: «май — июль 2026» — июль 2026 года.
    """
    found = next(
        (
            (name, rows, label)
            for name, rows in sheets.items()
            if (label := _average_label(rows[:8])) is not None
        ),
        None,
    )
    if found is None:
        raise SheetFormatError("таблица 13-01: нет листа «в среднем за период»")
    name, rows, label = found
    head = rows[:8]
    year, month = _period_end(label)
    column = next(
        (
            index
            for row in head
            for index, cell in enumerate(row)
            if isinstance(cell, str) and re.search(r"уровень\s+безработицы", cell, re.IGNORECASE)
        ),
        None,
    )
    if column is None:
        raise SheetFormatError(f"таблица 13-01, лист «{name}»: нет столбца уровня безработицы")

    records = []
    for row in rows:
        if not row or not isinstance(row[0], str):
            continue
        code = territory_code(row[0])
        if code is None or column >= len(row):
            continue
        value, hidden = parse_cell(row[column], where=f"{name}: {row[0]}")
        if value is None and not hidden:
            continue
        records.append(
            {
                "measure": measure,
                "territory_code": code,
                "year": year,
                "period_kind": "month",
                "period": month,
                "value": value,
                "hidden": hidden,
                "preliminary": False,
            }
        )
    frame = pd.DataFrame(records, columns=list(PARSED_COLUMNS)).drop_duplicates(
        subset=["territory_code"]
    )
    missing = sorted(region_codes() - set(frame["territory_code"]))
    if missing:
        raise SheetFormatError(f"таблица 13-01, лист «{name}»: нет субъектов {', '.join(missing)}")
    details = {
        "sheet": name,
        "title": re.sub(r"\s+", " ", label).strip(),
        "values": int(frame["value"].notna().sum()),
        "hidden": int(frame["hidden"].sum()),
        "regions": len(set(frame["territory_code"]) & region_codes()),
        "years": [year, year],
        "period": f"{year}-{month:02d}",
        "preliminary_years": [],
        "unmatched": [],
    }
    return frame, details


def _average_label(head: list[list[Any]]) -> str | None:
    """Подпись периода листа «в среднем за период» в шапке."""
    return next(
        (
            cell
            for row in head
            for cell in row
            if isinstance(cell, str) and "в среднем за" in cell.lower()
        ),
        None,
    )


def _period_end(label: str) -> tuple[int, int]:
    """Последний месяц и год подписи «май 2026 г. - июль 2026 г.»."""
    words = _MONTH_WORD.findall(label)
    years = _YEAR.findall(label)
    if not words or not years:
        raise SheetFormatError(f"таблица 13-01: период «{label}» не распознан")
    stem = words[-1].lower()
    month = next(number for prefix, number in _MONTH_STEMS if stem.startswith(prefix))
    return int(years[-1]), month


def is_preliminary(note: str, year: int) -> bool:
    """
    Сноска отмечает значения года как предварительные.

    Сноска бывает общей для нескольких годов («за 2017-2024 гг. — третья оценка, за 2025 г. —
    вторая оценка»): решает часть, где назван этот год, а без годов — сноска целиком.
    """
    if not note:
        return False
    clauses = [clause for clause in re.split(r"[;,]", note) if clause.strip()]
    named = [clause for clause in clauses if _mentions(clause, year)]
    if not named and any(_YEAR.search(clause) for clause in clauses):
        return False
    return any(
        _PRELIMINARY.search(clause) and not _FINAL.search(clause) for clause in (named or [note])
    )


def _mentions(text: str, year: int) -> bool:
    for first, last in _YEAR_RANGE.findall(text):
        if int(first) <= year <= int(last):
            return True
    return str(year) in _YEAR.findall(_YEAR_RANGE.sub("", text))


def _index_tables(files: Path) -> dict[str, Path]:
    """Файлы таблиц по номеру в начале имени: «12-01 среднемесячная заработная плата.xlsx»."""
    found: dict[str, Path] = {}
    for path in sorted(files.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".xls", ".xlsx"}:
            continue
        match = _TABLE_CODE.match(path.name)
        if match is not None:
            found.setdefault(match.group(1), path)
    return found


def _order(candidate: Candidate) -> tuple[int, int]:
    month, year = (int(part) for part in candidate.release_code.split("-"))
    return year, month
