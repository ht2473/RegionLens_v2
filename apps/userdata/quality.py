"""
Профиль качества ряда для вкладки «Проверки»: матрица «субъект × год» картинкой.

Клетка — есть значение, скрыто (строка есть, а числа нет: прочерк, «нет данных», код-маска
вместо числа) или нет (строки нет). Столбцы — субъекты в порядке справочника, между
федеральными округами — просвет и подпись округа; строки — годы ряда. Под картинкой — доля
клеток со значением, смена состава субъектов и расхождение суммы субъектов со значением
страны из «первого взгляда».
"""

from __future__ import annotations

from typing import Any

from django.http import Http404

from apps.warehouse.duckdb_client import WarehouseNotBuiltError

from . import glance
from .models import Dataset, DatasetVersion

# Клетка матрицы, её шаг и просвет между округами в единицах чертежа.
CELL = 6
STEP = 7
DISTRICT_GAP = 5
# Поле слева под подписи лет и снизу под подписи округов.
LEFT = 30
BOTTOM = 16
# Подписи лет — первый, последний и каждые столько лет между ними.
YEAR_LABEL_EVERY = 5
# Масштаб картинки на странице: единица чертежа — столько пикселей.
SCALE = 1.25

PRESENT, HIDDEN, ABSENT = "present", "hidden", "absent"


def profile(dataset: Dataset, version: DatasetVersion, wanted: str = "") -> dict[str, Any] | None:
    """
    Профиль ведущего ряда показателя: ``wanted`` — код ряда; по умолчанию — первый ряд
    с замечанием, иначе первый. Значений нет — ``None``.
    """
    from apps.analytics.selectors import region_rows
    from apps.catalog.models import Territory
    from apps.warehouse.queries import series_year_grid

    looks = glance.glance(dataset, version)["looks"]
    if not looks:
        return None
    chosen = next((look for look in looks if look.series.record.code == wanted), None)
    if chosen is None:
        chosen = next((look for look in looks if look.kind_note or look.composition_text), looks[0])
    record = chosen.series.record
    if not record.first_year or not record.last_year:
        return None
    years = list(range(record.first_year, record.last_year + 1))
    try:
        grid = series_year_grid(chosen.series.key, years[0], years[-1])
    except Http404, WarehouseNotBuiltError:
        return None

    short = {
        district.code: district.short_code for district in Territory.objects.federal_districts()
    }
    columns: list[dict[str, Any]] = []
    districts: list[dict[str, Any]] = []
    counts = {PRESENT: 0, HIDDEN: 0, ABSENT: 0}
    left = LEFT
    for region in region_rows():
        if not districts or districts[-1]["code"] != region["district_code"]:
            if districts:
                left += DISTRICT_GAP
            districts.append(
                {
                    "code": region["district_code"],
                    "text": short.get(region["district_code"], ""),
                    "name": region["district_name"],
                    "start": left,
                    "end": left,
                }
            )
        points = grid["regions"].get(region["code"], {})
        cells = []
        for row, year in enumerate(years):
            state = _state(points.get(year))
            counts[state] += 1
            cells.append({"y": row * STEP, "state": state})
        columns.append({"name": region["name"], "x": left, "cells": cells})
        districts[-1]["end"] = left + CELL
        left += STEP
    height = len(years) * STEP
    total = sum(counts.values())
    width = left + STEP
    return {
        "look": chosen,
        "choices": [(look.series.record.code, look.series.full_title) for look in looks],
        "columns": columns,
        "years": [
            {"y": row * STEP + CELL, "year": year}
            for row, year in enumerate(years)
            if row in {0, len(years) - 1}
            or (year % YEAR_LABEL_EVERY == 0 and 1 < row < len(years) - 2)
        ],
        "districts": [
            # Строкой: дробь шаблон записал бы с запятой, и подпись рассыпалась бы по буквам.
            {**item, "x": f"{(item['start'] + item['end']) / 2:.1f}", "y": height + BOTTOM - 4}
            for item in districts
        ],
        "width": width,
        "height": height + BOTTOM,
        "shown_width": round(width * SCALE),
        "shown_height": round((height + BOTTOM) * SCALE),
        "cell": CELL,
        "counts": counts,
        "share": round(100 * counts[PRESENT] / total) if total else 0,
        "first_year": years[0],
        "last_year": years[-1],
    }


def _state(point: dict[str, Any] | None) -> str:
    """Есть значение; скрыто — строка есть, числа нет; нет — строки нет."""
    if point is None:
        return ABSENT
    return PRESENT if point["value"] is not None else HIDDEN
