"""
Рабочий стол раздела «Свои данные»: недавние исследования и таблицы карточками.

Миниатюра — плиточная карта ряда за последний год: квадрат на субъект, цвет — ступень
шкалы, как на карте. Плитки легче контуров: на столе бывает два десятка карточек.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.http import Http404

from apps.warehouse.duckdb_client import WarehouseNotBuiltError

from . import studies
from .models import Dataset, DatasetVersion, Study

# Шаг плитки миниатюры в единицах чертежа и сторона квадрата: между плитками — просвет.
TILE_STEP = 10
TILE_SIZE = 9
# Ступеней шкалы миниатюры.
THUMB_CLASSES = 5
# Названий таблиц под карточкой исследования.
SOURCES_SHOWN = 2


@dataclass(slots=True)
class Thumbnail:
    """Плиточная карта ряда: плитки с координатами и ступенью шкалы, размер чертежа."""

    tiles: list[dict[str, Any]]
    width: int
    height: int


@dataclass(slots=True)
class StudyCard:
    """Исследование на столе: число карточек, свои таблицы в нём и миниатюра."""

    study: Study
    cards: int
    sources: list[str] = field(default_factory=list)
    thumbnail: Thumbnail | None = None


@dataclass(slots=True)
class TableRow:
    """Таблица на столе: собрана или черновик, в скольких исследованиях, куда продолжить."""

    dataset: Dataset
    version: DatasetVersion | None
    built: bool
    studies: int = 0
    continue_url: str = ""


def thumbnail(key: str) -> Thumbnail | None:
    """Миниатюра ряда за последний год; ряда нет или значений нет — ``None``."""
    from apps.analytics.selectors import region_rows
    from apps.maps.classification import classify, sequential_palette
    from apps.warehouse.queries import region_panel

    try:
        panel = region_panel(key)
    except Http404, WarehouseNotBuiltError:
        return None
    if not panel:
        return None
    values = panel[max(panel)]
    classification = classify(list(values.values()), class_count=THUMB_CLASSES)
    palette = sequential_palette(classification.class_count) if classification else []
    tiles = []
    for row in region_rows():
        if row.get("tile_x") is None or row.get("tile_y") is None:
            continue
        value = values.get(row["code"])
        index = (
            classification.class_of(value)
            if classification is not None and value is not None
            else None
        )
        tiles.append(
            {
                "x": row["tile_x"] * TILE_STEP,
                "y": row["tile_y"] * TILE_STEP,
                "step": palette[index].rsplit("-", 1)[1] if index is not None else "",
            }
        )
    if not tiles:
        return None
    return Thumbnail(
        tiles=tiles,
        width=max(tile["x"] for tile in tiles) + TILE_STEP,
        height=max(tile["y"] for tile in tiles) + TILE_STEP,
    )


def study_cards(items: list[Study], titles: dict[str, str]) -> list[StudyCard]:
    """
    Карточки исследований: миниатюра — по ряду первой карты (иначе первой карточки
    с рядом), под названием — свои таблицы исследования, кроме тёзки самого исследования
    (новое исследование называется по таблице). ``titles`` — код таблицы → её название.
    """
    found = []
    for study in items:
        blocks = studies.blocks_of(study)
        keyed = [block for block in blocks if studies.series_keys(block)]
        first = next((block for block in keyed if block.get("target") == "map"), None)
        first = first or (keyed[0] if keyed else None)
        codes = studies.dataset_codes(study)
        found.append(
            StudyCard(
                study=study,
                cards=sum(1 for block in blocks if block["kind"] != studies.TEXT),
                sources=[
                    titles[code]
                    for code in sorted(codes)
                    if code in titles and titles[code].casefold() != study.title.casefold()
                ][:SOURCES_SHOWN],
                thumbnail=thumbnail(studies.series_keys(first)[0]) if first else None,
            )
        )
    return found


def table_rows(datasets: list[Dataset], items: list[Study]) -> list[TableRow]:
    """Таблицы: собранные — с числом исследований, где они есть; черновики — с шагом мастера."""
    from .pages import continue_url

    used: dict[str, int] = {}
    for study in items:
        for code in studies.dataset_codes(study):
            used[code] = used.get(code, 0) + 1
    rows = []
    for dataset in datasets:
        version = dataset.current_version
        built = version is not None and version.state == DatasetVersion.State.BUILT
        rows.append(
            TableRow(
                dataset=dataset,
                version=version,
                built=built,
                studies=used.get(dataset.code, 0),
                continue_url="" if built else continue_url(dataset, version),
            )
        )
    return rows
