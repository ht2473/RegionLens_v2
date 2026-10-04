"""
«Скачать таблицу»: очищенная длинная таблица набора — территория с кодами справочника,
год, период, показатель, разрезы, значение, единица и пометка. CSV отдаётся потоком,
XLSX — для таблиц не больше ``XLSX_ROWS`` строк.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from django.utils.translation import get_language, gettext
from openpyxl import Workbook

from apps.catalog.constants import ValueFlag, ValueQuality
from apps.exports.renderers.safety import safe_cell, safe_text
from apps.warehouse.duckdb_client import DataSource, dataset_connection

from . import naming
from .models import DatasetSeries, DatasetVersion

# Строк в книге Excel не больше этого: больше — только CSV.
XLSX_ROWS = 300_000
BATCH_ROWS = 20_000
BOM = chr(0xFEFF)


@dataclass(frozen=True, slots=True)
class Layout:
    """Столбцы выгрузки и описание рядов по ключу."""

    headers: list[str]
    slice_headers: list[str]
    series: dict[str, DatasetSeries]


def layout(version: DatasetVersion) -> Layout:
    """Столбцы: разрезы всех рядов по порядку появления."""
    records = list(version.series.order_by("order"))
    slice_headers: list[str] = []
    for record in records:
        for header, _value in record.slices:
            name = header or gettext("Группа")
            if name not in slice_headers:
                slice_headers.append(name)
    headers = [
        gettext("Территория"),
        gettext("Код территории (ISO 3166-2 у субъектов)"),
        gettext("ОКАТО"),
        gettext("Уровень"),
        gettext("Год"),
        gettext("Период"),
        gettext("Показатель"),
        *slice_headers,
        gettext("Значение"),
        gettext("Единица"),
        gettext("Пометка"),
    ]
    prefix = f"u:{version.dataset.code}:"
    return Layout(
        headers=headers,
        slice_headers=slice_headers,
        series={prefix + record.code: record for record in records},
    )


def count_rows(source: DataSource) -> int:
    """Строк в выгрузке: наблюдения и строки вне справочника."""
    connection = dataset_connection(source)
    row = connection.execute(
        "SELECT (SELECT count(*) FROM fact_observation) + (SELECT count(*) FROM user_outside)"
    ).fetchone()
    return int(row[0]) if row else 0


def rows(source: DataSource, plan: Layout) -> Iterator[list[Any]]:
    """Строки выгрузки: сначала территории справочника, затем строки вне справочника."""
    english = get_language() == "en"
    levels = {
        "country": gettext("страна"),
        "federal_district": gettext("федеральный округ"),
        "region": gettext("субъект"),
    }
    connection = dataset_connection(source)
    cursor = connection.execute(
        """
        SELECT f.series_key, t.name_ru, t.name_en, f.territory_code, t.okato, t.level,
               t.is_aggregate, f.year, f.value, f.quality, f.flags
        FROM fact_observation AS f
        JOIN dim_territory    AS t USING (territory_code)
        ORDER BY f.series_key, t.display_order, f.territory_code, f.year
        """
    )
    while batch := cursor.fetchmany(BATCH_ROWS):
        for (
            key,
            name_ru,
            name_en,
            code,
            okato,
            level,
            aggregate,
            year,
            value,
            quality,
            flags,
        ) in batch:
            record = plan.series.get(key)
            if record is None:
                continue
            level_text = levels.get(level, level)
            if aggregate:
                level_text = gettext("итог с автономными округами")
            yield _row(
                plan,
                record,
                territory=(name_en if english and name_en else name_ru),
                code=code,
                okato=okato or "",
                level=level_text,
                year=year,
                value=value,
                mark=_mark(quality, flags),
            )
    cursor = connection.execute(
        "SELECT series_key, label, year, value, quality FROM user_outside "
        "ORDER BY series_key, label, year"
    )
    while batch := cursor.fetchmany(BATCH_ROWS):
        for key, label, year, value, quality in batch:
            record = plan.series.get(key)
            if record is None:
                continue
            yield _row(
                plan,
                record,
                territory=label,
                code="",
                okato="",
                level="",
                year=year,
                value=value,
                mark=_join(gettext("вне справочника"), _mark(quality, 0)),
            )


def csv_stream(source: DataSource, plan: Layout) -> Iterator[bytes]:
    """CSV по частям: метка порядка байтов, шапка, строки; разделитель — запятая."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, dialect="unix", quoting=csv.QUOTE_MINIMAL)
    buffer.write(BOM)
    writer.writerow([safe_text(header) for header in plan.headers])
    for number, row in enumerate(rows(source, plan), start=1):
        writer.writerow([_csv_cell(cell) for cell in row])
        if number % BATCH_ROWS == 0:
            yield buffer.getvalue().encode("utf-8")
            buffer.seek(0)
            buffer.truncate()
    yield buffer.getvalue().encode("utf-8")


def xlsx_bytes(source: DataSource, plan: Layout) -> bytes:
    """Книга Excel с одним листом таблицы."""
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(gettext("Таблица"))
    sheet.append([safe_cell(header) for header in plan.headers])
    for row in rows(source, plan):
        sheet.append([safe_cell(cell) for cell in row])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _row(
    plan: Layout,
    record: DatasetSeries,
    *,
    territory: str,
    code: str,
    okato: str,
    level: str,
    year: int,
    value: float | None,
    mark: str,
) -> list[Any]:
    slices = {header or gettext("Группа"): value for header, value in record.slices}
    return [
        territory,
        code,
        okato,
        level,
        int(year),
        naming.period_text(record.period) or gettext("год"),
        record.title,
        *[slices.get(header, "") for header in plan.slice_headers],
        value,
        record.unit,
        mark,
    ]


def _mark(quality: int, flags: int) -> str:
    """Пометка значения словами."""
    parts = []
    if quality == ValueQuality.NO_DATA:
        parts.append(gettext("нет данных"))
    elif quality == ValueQuality.HIDDEN:
        parts.append(gettext("скрыто источником"))
    flag = ValueFlag(int(flags or 0))
    if ValueFlag.COMPUTED in flag:
        parts.append(gettext("расчёт RegionLens"))
    if ValueFlag.FROZEN_DENOMINATOR in flag:
        parts.append(gettext("численность прошлого года"))
    return "; ".join(parts)


def _join(*parts: str) -> str:
    return "; ".join(part for part in parts if part)


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text not in {"", "-0"} else ("0" if value == 0 else repr(value))
    if isinstance(value, int):
        return str(value)
    return safe_text(str(value))
