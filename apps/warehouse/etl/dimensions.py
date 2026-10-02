"""
Измерения склада: территории из PostgreSQL, остальное — из набора по правилам разбора.

На Python разбираются только уникальные значения, не два миллиона наблюдений.
"""

from __future__ import annotations

import logging
import re

import duckdb
import pandas as pd

from apps.catalog.constants import UNSPECIFIED_UNIT_NAME
from apps.catalog.models import Territory
from apps.core.utils.text import make_slug, normalize_text

from .catalog_names import (
    full_title_en,
    indicator_name_en,
    publication_name_en,
    section_name_en,
    subsection_name_en,
    unit_name_en,
    unit_short_name_en,
)
from .classify import (
    UnitClassification,
    classify_unit,
    detect_polarity,
    make_code,
    make_series_key,
)
from .typography import fix_subsection

logger = logging.getLogger(__name__)

# Год выпуска указан в конце названия издания: «Регионы России. ... 2025».
_EDITION_YEAR_PATTERN = re.compile(r"\s(\d{4})\s*$")


def load_territories(connection: duckdb.DuckDBPyConnection) -> int:
    """Загрузить справочник территорий из PostgreSQL в измерение склада."""
    rows = [
        {
            "territory_code": territory.code,
            "source_name": territory.source_name,
            "name_ru": territory.name_ru,
            "name_en": territory.name_en or None,
            "abbreviation": territory.abbreviation or None,
            "level": territory.level,
            "territory_type": territory.territory_type,
            "district_code": territory.parent.code if territory.parent else None,
            "is_aggregate": territory.is_aggregate,
            "okato": territory.okato or None,
            "area_km2": float(territory.area_km2) if territory.area_km2 else None,
            "utc_offset": territory.utc_offset,
            "data_since_year": territory.data_since_year,
            "display_order": territory.display_order,
        }
        for territory in Territory.objects.select_related("parent").all()
    ]

    if not rows:
        raise RuntimeError(
            "Справочник территорий пуст. Выполните команду: python manage.py seed_reference"
        )

    frame = pd.DataFrame(rows)
    connection.register("tmp_territories", frame)
    connection.execute("INSERT INTO dim_territory SELECT * FROM tmp_territories")
    connection.unregister("tmp_territories")

    logger.info("Загружено территорий: %d", len(rows))
    return len(rows)


def build_sections(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Построить измерение разделов с устойчивым кодом и слагом для каждой строки источника."""
    frame = connection.execute(
        """
        SELECT section AS source_name,
               count(DISTINCT indicator_code) AS indicator_count,
               count(*) AS observation_count
        FROM src
        GROUP BY 1
        ORDER BY 1
        """
    ).df()

    frame["source_name"] = frame["source_name"].map(normalize_text)
    frame["section_code"] = frame["source_name"].map(lambda value: make_code("sec", value))
    frame["name_ru"] = frame["source_name"]
    frame["name_en"] = frame["source_name"].map(section_name_en)
    frame["slug"] = frame["source_name"].map(lambda value: make_slug(value, max_length=120))
    frame["series_count"] = 0

    payload = frame[
        ["section_code", "source_name", "name_ru", "name_en", "indicator_count", "series_count"]
    ]
    connection.register("tmp_sections", payload)
    connection.execute("INSERT INTO dim_section SELECT * FROM tmp_sections")
    connection.unregister("tmp_sections")

    logger.info("Разделов: %d", len(frame))
    return frame


def build_editions(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Построить измерение выпусков: издание и год — для упорядочивания по свежести."""
    frame = connection.execute(
        """
        SELECT source AS source_name,
               count(*) AS observation_count,
               min(year) AS first_year,
               max(year) AS last_year
        FROM src
        GROUP BY 1
        ORDER BY 1
        """
    ).df()

    frame["source_name"] = frame["source_name"].map(normalize_text)
    frame["edition_year"] = frame["source_name"].map(_extract_edition_year)
    frame["publication_ru"] = frame["source_name"].map(_extract_publication_name)
    frame["publication_en"] = frame["publication_ru"].map(publication_name_en)
    frame["edition_code"] = frame["source_name"].map(lambda value: make_code("ed", value))
    frame["edition_label"] = frame["edition_year"].map(str)

    payload = frame[
        [
            "edition_code",
            "source_name",
            "publication_ru",
            "publication_en",
            "edition_year",
            "observation_count",
            "first_year",
            "last_year",
            "edition_label",
        ]
    ]
    connection.register("tmp_editions", payload)
    connection.execute(
        """
        INSERT INTO dim_edition (
            edition_code, source_name, publication_ru, publication_en, edition_year,
            observation_count, first_year, last_year, edition_label
        )
        SELECT * FROM tmp_editions
        """
    )
    connection.unregister("tmp_editions")

    logger.info("Выпусков изданий: %d", len(frame))
    return frame


def build_unit_scales(connection: duckdb.DuckDBPyConnection) -> int:
    """
    Построить таблицу коэффициентов приведения единиц измерения.

    Коэффициент — на каждую строку единицы, а не на ряд: выпуски объявляют единицу
    по-разному («Тысяч тонн; для значений в целом по России: млн т» и «Тысяч тонн»).
    """
    frame = connection.execute(
        "SELECT DISTINCT indicator_unit AS source_unit FROM src WHERE indicator_unit IS NOT NULL"
    ).df()

    classifications = [classify_unit(value) for value in frame["source_unit"]]
    frame["country_scale"] = [item.country_scale for item in classifications]
    frame["country_scale_unknown"] = [item.country_scale_unknown for item in classifications]

    connection.register("tmp_unit_scales", frame)
    connection.execute("CREATE OR REPLACE TABLE map_unit_scale AS SELECT * FROM tmp_unit_scales")
    connection.unregister("tmp_unit_scales")

    scaled = int((frame["country_scale"] != 1.0).sum())
    logger.info("Единиц измерения в источнике: %d, из них с приведением: %d", len(frame), scaled)
    return len(frame)


def build_series(connection: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Построить измерения показателей, рядов и единиц измерения.

    Единица ряда — самое частое непустое объявление в нём, заглушки ``ND`` не в счёт.
    """
    # --- Наиболее частая единица измерения в пределах ряда ------------------------------
    raw = connection.execute(
        """
        WITH per_unit AS (
            SELECT indicator_code,
                   coalesce(subsection, '') AS subsection,
                   any_value(indicator_name) AS indicator_name,
                   any_value(section)        AS section,
                   indicator_unit,
                   count(*)                  AS n
            FROM src
            GROUP BY 1, 2, 5
        ),
        ranked AS (
            SELECT *,
                   row_number() OVER (
                       PARTITION BY indicator_code, subsection
                       -- Заглушка ND уступает любому явно указанному значению.
                       ORDER BY (indicator_unit = 'ND') ASC, n DESC, indicator_unit ASC
                   ) AS priority
            FROM per_unit
        )
        SELECT indicator_code, subsection, indicator_name, section, indicator_unit
        FROM ranked
        WHERE priority = 1
        ORDER BY indicator_code, subsection
        """
    ).df()

    raw["indicator_name"] = raw["indicator_name"].map(normalize_text)
    raw["section"] = raw["section"].map(normalize_text)
    raw["subsection_clean"] = raw["subsection"].map(_clean_subsection)

    # --- Классификация единиц измерения ---------------------------------------------------
    classifications = [
        classify_unit(
            row.indicator_unit,
            indicator_name=row.indicator_name,
            subsection=row.subsection_clean or "",
        )
        for row in raw.itertuples()
    ]
    _inherit_total_units(raw, classifications)

    raw["unit_code"] = [item.code for item in classifications]

    units = pd.DataFrame(
        {
            "unit_code": [item.code for item in classifications],
            "source_name": [item.source_name for item in classifications],
            "name_ru": [item.name_ru for item in classifications],
            "name_en": [unit_name_en(item.name_ru) for item in classifications],
            "short_name_ru": [item.short_name_ru for item in classifications],
            "short_name_en": [unit_short_name_en(item.short_name_ru) for item in classifications],
            "kind": [item.kind for item in classifications],
            "multiplier": [item.multiplier for item in classifications],
            "derived_from_name": [item.derived_from_name for item in classifications],
            "country_scale": [item.country_scale for item in classifications],
            "country_scale_unknown": [item.country_scale_unknown for item in classifications],
        }
    ).drop_duplicates(subset=["unit_code"])
    units["observation_count"] = 0

    connection.register("tmp_units", units)
    # Столбцы перечислены явно: порядок в таблице и в датафрейме может разойтись.
    connection.execute(
        """
        INSERT INTO dim_unit (
            unit_code, source_name, name_ru, name_en, short_name_ru, short_name_en,
            kind, multiplier, derived_from_name, country_scale, country_scale_unknown,
            observation_count
        )
        SELECT
            unit_code, source_name, name_ru, name_en, short_name_ru, short_name_en,
            kind, multiplier, derived_from_name, country_scale, country_scale_unknown,
            observation_count
        FROM tmp_units
        """
    )
    connection.unregister("tmp_units")

    # --- Ряды ---------------------------------------------------------------------------------
    raw["series_key"] = [
        make_series_key(row.indicator_code, row.subsection_clean) for row in raw.itertuples()
    ]
    raw["section_code"] = raw["section"].map(lambda value: make_code("sec", value))
    raw["has_subsection"] = raw["subsection_clean"].notna() & (raw["subsection_clean"] != "")
    # Пропуск pandas — в None, иначе в таблице окажется строка «nan».
    raw["subsection_ru"] = raw["subsection_clean"].astype(object).where(raw["has_subsection"], None)
    raw["full_title_ru"] = [
        f"{row.indicator_name} — {normalize_text(row.subsection_ru)}"
        if row.has_subsection
        else row.indicator_name
        for row in raw.itertuples()
    ]
    # Английские наименования — из справочника переводов.
    raw["indicator_name_en"] = raw["indicator_code"].map(indicator_name_en)
    raw["subsection_en"] = [
        subsection_name_en(row.subsection_ru) if row.has_subsection else None
        for row in raw.itertuples()
    ]
    raw["full_title_en"] = [
        full_title_en(row.indicator_code, row.subsection_ru if row.has_subsection else None)
        for row in raw.itertuples()
    ]
    raw["polarity"] = [
        detect_polarity(row.indicator_name, row.subsection_ru) for row in raw.itertuples()
    ]

    series = raw[
        [
            "series_key",
            "indicator_code",
            "section_code",
            "unit_code",
            "indicator_name",
            "indicator_name_en",
            "subsection_ru",
            "subsection_en",
            "has_subsection",
            "full_title_ru",
            "full_title_en",
            "polarity",
        ]
    ].rename(columns={"indicator_name": "indicator_name_ru"})

    # Разрез встречается с разными формулировками показателя при одном ключе ряда.
    series = series.drop_duplicates(subset=["series_key"])

    connection.register("tmp_series", series)
    columns = ", ".join(series.columns)
    connection.execute(f"INSERT INTO dim_series ({columns}) SELECT {columns} FROM tmp_series")
    connection.unregister("tmp_series")

    # Соответствие пары «показатель + разрез» источника ключу ряда, вычисленному на Python.
    mapping = (
        raw[["indicator_code", "subsection", "series_key"]]
        .rename(columns={"subsection": "subsection_raw"})
        .drop_duplicates()
    )
    connection.register("tmp_map_series", mapping)
    connection.execute("CREATE OR REPLACE TABLE map_series AS SELECT * FROM tmp_map_series")
    connection.unregister("tmp_map_series")

    # --- Показатели ------------------------------------------------------------------------------
    indicators = (
        raw.groupby("indicator_code")
        .agg(
            name_ru=("indicator_name", "first"),
            section_code=("section_code", "first"),
            series_count=("series_key", "nunique"),
        )
        .reset_index()
    )
    indicators["name_en"] = indicators["indicator_code"].map(indicator_name_en)
    indicators["observation_count"] = 0
    indicators["first_year"] = None
    indicators["last_year"] = None

    payload = indicators[
        [
            "indicator_code",
            "name_ru",
            "name_en",
            "section_code",
            "series_count",
            "observation_count",
            "first_year",
            "last_year",
        ]
    ]
    connection.register("tmp_indicators", payload)
    connection.execute("INSERT INTO dim_indicator SELECT * FROM tmp_indicators")
    connection.unregister("tmp_indicators")

    logger.info(
        "Показателей: %d, рядов: %d, единиц измерения: %d",
        len(indicators),
        len(series),
        len(units),
    )
    return series, units


def refresh_dimension_counters(connection: duckdb.DuckDBPyConnection) -> None:
    """Пересчитать сводные счётчики измерений по загруженным наблюдениям."""
    connection.execute(
        """
        UPDATE dim_indicator AS d
        SET observation_count = agg.observations,
            first_year        = agg.first_year,
            last_year         = agg.last_year
        FROM (
            SELECT indicator_code,
                   count(*)  AS observations,
                   min(year) AS first_year,
                   max(year) AS last_year
            FROM fact_observation
            WHERE value IS NOT NULL
            GROUP BY 1
        ) AS agg
        WHERE d.indicator_code = agg.indicator_code
        """
    )

    connection.execute(
        """
        UPDATE dim_section AS d
        SET series_count = agg.series_count
        FROM (
            SELECT section_code, count(*) AS series_count
            FROM dim_series
            GROUP BY 1
        ) AS agg
        WHERE d.section_code = agg.section_code
        """
    )

    connection.execute(
        """
        UPDATE dim_unit AS d
        SET observation_count = agg.observations
        FROM (
            SELECT s.unit_code, count(*) AS observations
            FROM fact_observation AS f
            JOIN dim_series AS s USING (series_key)
            GROUP BY 1
        ) AS agg
        WHERE d.unit_code = agg.unit_code
        """
    )


# ---------------------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------------------


# Разрезы-части итога: «В том числе в возрасте, лет: 15-19» при итоге «Всего, тыс. человек».
_PART_PREFIXES = ("в том числе", "из них", "из него")


def _inherit_total_units(raw: pd.DataFrame, classifications: list[UnitClassification]) -> None:
    """
    Дать частям итога единицу итога того же показателя.

    Источник часто называет единицу только у строки «Всего», а у частей оставляет ND.
    """
    totals: dict[str, UnitClassification] = {}
    for row, item in zip(raw.itertuples(), classifications, strict=True):
        if (
            _lowered(row.subsection_clean).startswith("всего")
            and item.name_ru != UNSPECIFIED_UNIT_NAME
        ):
            totals.setdefault(str(row.indicator_code), item)

    for index, row in enumerate(raw.itertuples()):
        total = totals.get(str(row.indicator_code))
        if (
            total is not None
            and classifications[index].name_ru == UNSPECIFIED_UNIT_NAME
            and _lowered(row.subsection_clean).startswith(_PART_PREFIXES)
        ):
            classifications[index] = total


def _lowered(value: object) -> str:
    """Строка в нижнем регистре; пропуск pandas — пустая строка."""
    return value.lower() if isinstance(value, str) else ""


def _extract_edition_year(source_name: str) -> int:
    """Извлечь год выпуска из названия издания."""
    match = _EDITION_YEAR_PATTERN.search(source_name)
    return int(match.group(1)) if match else 0


def _extract_publication_name(source_name: str) -> str:
    """Отделить название издания от года выпуска."""
    return _EDITION_YEAR_PATTERN.sub("", source_name).strip()


def _clean_subsection(value: str | None) -> str | None:
    """
    Привести значение разреза к каноническому виду; заглушка ``CD`` — разреза нет.

    Переносы вёрстки исправляются до построения ключа: один разрез, напечатанный
    в разных выпусках с разными переносами, — один ряд.
    """
    normalized = normalize_text(value)
    if not normalized or normalized == "CD":
        return None
    return fix_subsection(normalized)
