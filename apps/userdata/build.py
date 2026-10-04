"""
Сборка набора: извлечённые наблюдения и описание показателей → файл DuckDB со схемой склада.

Схема — та же, что у склада (``apps/warehouse/sql/schema.sql``), поэтому выборки холста
и витрины (``marts.build_*``) работают на файле набора без изменений. В файл попадают
территории справочника, ряды таблицы, область без автономных округов (для сумм — итог
за вычетом округов, с пометкой «рассчитано»), пересчёты сумм на жителей и на км²
и строки вне справочника — отдельной таблицей: на карту и в рейтинг они не попадают.
Всё считается в DuckDB одним проходом по всем рядам. Файл неизменяем: новая сборка
пишет новый файл.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
from django.conf import settings
from django.db import transaction
from django.utils import timezone, translation

from apps.core.templatetags.formatting import LARGE_VALUE_THRESHOLD, SMALL_VALUE_THRESHOLD
from apps.warehouse.duckdb_client import close_dataset
from apps.warehouse.etl import marts
from apps.warehouse.etl.dimensions import load_territories

from . import extract, indicators, jobs, matching, naming
from .models import Dataset, DatasetSeries, DatasetVersion

logger = logging.getLogger(__name__)

SCHEMA = Path(settings.BASE_DIR) / "apps" / "warehouse" / "sql" / "schema.sql"
SECTION_CODE = "userdata"
# Множитель и подпись пересчёта суммы.
PER_FACTORS = {
    DatasetSeries.Derived.PER_1000.value: 1_000,
    DatasetSeries.Derived.PER_100000.value: 100_000,
}
PER_SUFFIXES = {
    DatasetSeries.Derived.PER_1000.value: "-p1k",
    DatasetSeries.Derived.PER_100000.value: "-p100k",
    DatasetSeries.Derived.PER_KM2.value: "-km2",
}
# Признаки значения в запросах сборки — как ValueFlag и fact_observation.flags склада:
# 2 — рассчитано (область без округов, пересчёт), 4 — знаменатель заморожен.

# Наблюдения таблицы; область без округов у сумм — итог за вычетом всех округов того же года,
# если самой области в ряду нет.
FACTS_SQL = """
    CREATE TEMP TABLE facts AS
    SELECT p.series_key, r.territory_code, CAST(r.year AS INTEGER) AS year, r.value,
           CAST(r.quality AS INTEGER) AS quality, 0 AS flags
    FROM raw AS r
    JOIN plan AS p ON p.source = r.series AND p.derived = ''
    WHERE r.territory_code IS NOT NULL;

    INSERT INTO facts
    WITH totals AS (
        SELECT f.series_key, n.total, n.alone, n.members, f.year, f.value
        FROM facts AS f
        JOIN plan AS p USING (series_key)
        JOIN (SELECT DISTINCT total, alone, members FROM nested) AS n
          ON f.territory_code = n.total
        WHERE p.kind = 'sum' AND p.derived = '' AND f.value IS NOT NULL
    ),
    parts AS (
        SELECT f.series_key, n.total, f.year, sum(f.value) AS value, count(f.value) AS filled
        FROM facts AS f
        JOIN nested AS n ON f.territory_code = n.member
        GROUP BY 1, 2, 3
    )
    SELECT t.series_key, t.alone, t.year, t.value - p.value, 0, 2
    FROM totals AS t
    JOIN parts  AS p USING (series_key, total, year)
    WHERE p.filled = t.members
      AND NOT EXISTS (
          SELECT 1 FROM facts AS x
          WHERE x.series_key = t.series_key AND x.territory_code = t.alone
      )
"""

# У доли или среднего область без округов не вычислить: она остаётся пустой.
ALONE_MISSING_SQL = """
    SELECT DISTINCT n.alone
    FROM facts AS f
    JOIN plan AS p USING (series_key)
    JOIN (SELECT DISTINCT total, alone FROM nested) AS n ON f.territory_code = n.total
    WHERE p.kind <> 'sum' AND p.derived = ''
      AND NOT EXISTS (
          SELECT 1 FROM facts AS x
          WHERE x.series_key = f.series_key AND x.territory_code = n.alone
      )
    ORDER BY 1
"""

# Пересчёт суммы на жителей: численность года значения, после последнего известного
# года — последняя (знаменатель заморожен, как у рядов склада), до первого — значения нет.
PER_CAPITA_SQL = """
    INSERT INTO facts
    WITH last AS (
        SELECT territory_code, max(year) AS last_year FROM population GROUP BY 1
    ),
    based AS (
        SELECT d.series_key AS derived_key, d.factor, f.territory_code, f.year, f.value,
               f.flags, least(f.year, l.last_year) AS base_year
        FROM plan AS d
        JOIN facts AS f ON f.series_key = d.base_key
        JOIN last  AS l USING (territory_code)
        WHERE d.factor > 0 AND f.value IS NOT NULL
    )
    SELECT b.derived_key, b.territory_code, b.year, b.value / p.population * b.factor, 0,
           b.flags | 2 | CASE WHEN b.base_year < b.year THEN 4 ELSE 0 END
    FROM based AS b
    JOIN population AS p ON p.territory_code = b.territory_code AND p.year = b.base_year
    WHERE p.population > 0
"""

PER_AREA_SQL = """
    INSERT INTO facts
    SELECT d.series_key, f.territory_code, f.year, f.value / a.area, 0, f.flags | 2
    FROM plan AS d
    JOIN facts AS f ON f.series_key = d.base_key
    JOIN areas AS a USING (territory_code)
    WHERE d.derived = 'perkm2' AND f.value IS NOT NULL AND a.area > 0
"""

OBSERVATIONS_SQL = """
    INSERT INTO fact_observation
    SELECT f.series_key, s.indicator_code, s.section_code, f.territory_code,
           t.level, t.district_code, t.is_aggregate, CAST(f.year AS SMALLINT),
           f.value, CAST(f.quality AS TINYINT), NULL, NULL, 0, CAST(f.flags AS TINYINT)
    FROM facts AS f
    JOIN dim_series    AS s USING (series_key)
    JOIN dim_territory AS t USING (territory_code)
    ORDER BY f.series_key, f.territory_code, f.year
"""

OUTSIDE_SQL = """
    CREATE TABLE user_outside AS
    SELECT p.series_key, r.label, CAST(r.year AS SMALLINT) AS year, r.value,
           CAST(r.quality AS TINYINT) AS quality
    FROM raw AS r
    JOIN plan AS p ON p.source = r.series AND p.derived = ''
    WHERE r.territory_code IS NULL
    ORDER BY 1, 2, 3
"""

# Итоги по рядам: значения, субъекты, годы, целые ли значения и середина — для точности.
SUMMARY_SQL = """
    SELECT f.series_key,
           count(f.value) AS valued,
           count(DISTINCT f.territory_code) FILTER (
               WHERE f.value IS NOT NULL AND t.level = 'region' AND NOT t.is_aggregate
           ) AS regions,
           min(f.year) FILTER (WHERE f.value IS NOT NULL) AS first_year,
           max(f.year) FILTER (WHERE f.value IS NOT NULL) AS last_year,
           bool_and(f.value = round(f.value)) AS integral,
           abs(median(f.value)) AS middle
    FROM fact_observation AS f
    JOIN dim_territory AS t USING (territory_code)
    GROUP BY 1
    ORDER BY 1
"""

TOTAL_SQL = """
    SELECT count(f.value),
           count(DISTINCT f.territory_code) FILTER (
               WHERE f.value IS NOT NULL AND t.level = 'region' AND NOT t.is_aggregate
           ),
           min(f.year) FILTER (WHERE f.value IS NOT NULL),
           max(f.year) FILTER (WHERE f.value IS NOT NULL)
    FROM fact_observation AS f
    JOIN dim_territory AS t USING (territory_code)
    JOIN plan AS p USING (series_key)
    WHERE p.derived = ''
"""


def build(version: DatasetVersion) -> None:
    """Собрать файл набора и ряды версии; прежний файл сборки удаляется."""
    started = time.perf_counter()
    if not extract.is_current(version):
        jobs.mark(version, extract=extract.extract(version))
        version.refresh_from_db(fields=["report"])
    plan = _plan(version, version.report["extract"])
    file_name = f"data-{timezone.now():%Y%m%d%H%M%S%f}.duckdb"
    path = version.directory / file_name
    try:
        summary = _write_file(path, version, plan, extract.read(version))
    except Exception:
        _remove(path)
        raise
    _save(version, plan, summary, file_name)
    logger.info(
        "userdata: собрана версия %s: рядов %s, значений %s, %.2f с",
        version.pk,
        len(plan),
        summary["values"],
        time.perf_counter() - started,
    )


# --- Ряды -----------------------------------------------------------------------------------------


def _plan(version: DatasetVersion, report: dict[str, Any]) -> list[dict[str, Any]]:
    """Ряды набора: из таблицы и пересчёты сумм, с описанием показателя."""
    described = {item.name: item for item in indicators.indicators_of(version)}
    plan: list[dict[str, Any]] = []
    for index, item in enumerate(report["series"]):
        indicator = described[item["indicator"]]
        base = {
            "source": index,
            "code": item["code"],
            "indicator": item["indicator"],
            "title": indicator.title,
            "unit": indicator.unit or item["unit"],
            "kind": indicator.kind,
            "polarity": indicator.polarity,
            "slices": [tuple(pair) for pair in item["slices"]],
            "period": item["period"],
            "derived": "",
            "base_code": "",
        }
        plan.append(base)
        if indicator.kind != indicators.SUM:
            continue
        for per in indicator.per:
            plan.append(
                {
                    **base,
                    "source": -1,
                    "code": base["code"] + PER_SUFFIXES[per],
                    "title": f"{indicator.title} {_per_title(per)}",
                    "kind": indicators.RELATIVE,
                    "derived": per,
                    "base_code": base["code"],
                }
            )
    prefix = f"u:{version.dataset.code}:"
    for order, item in enumerate(plan):
        item["order"] = order
        item["key"] = prefix + item["code"]
        item["base_key"] = prefix + item["base_code"] if item["base_code"] else ""
    return plan


def _per_title(per: str) -> str:
    """Подпись пересчёта в названии ряда — по-русски: название ряда хранится как в таблице."""
    with translation.override("ru"):
        return str(DatasetSeries.Derived(per).label)


# --- Файл набора -------------------------------------------------------------------------------


def _write_file(
    path: Path, version: DatasetVersion, plan: list[dict[str, Any]], raw: pa.Table
) -> dict[str, Any]:
    """Файл DuckDB со схемой склада, наблюдениями и витринами; вернуть итоги сборки."""
    connection = duckdb.connect(str(path))
    try:
        connection.execute(f"SET memory_limit = '{settings.USERDATA_PARSE_MEMORY}'")
        connection.execute("SET threads = 2")
        connection.execute("SET preserve_insertion_order = true")
        connection.execute(SCHEMA.read_text(encoding="utf-8"))
        load_territories(connection)
        _dimensions(connection, version, plan)
        connection.register("raw", raw)
        connection.register("plan", _plan_frame(plan))
        connection.register("nested", _nested_frame())
        connection.execute(FACTS_SQL)
        alone_missing = [row[0] for row in connection.execute(ALONE_MISSING_SQL).fetchall()]
        derived = {item["derived"] for item in plan if item["derived"]}
        if derived & set(PER_FACTORS):
            connection.register("population", _population_frame())
            connection.execute(PER_CAPITA_SQL)
        if DatasetSeries.Derived.PER_KM2.value in derived:
            connection.register("areas", _areas_frame())
            connection.execute(PER_AREA_SQL)
        connection.execute(OBSERVATIONS_SQL)
        connection.execute(OUTSIDE_SQL)
        marts.build_coverage(connection)
        marts.build_stats(connection)
        marts.build_ranks(connection)
        summary = _summary(connection)
        summary["alone_missing"] = alone_missing
        connection.execute("DROP TABLE facts")
        connection.executemany(
            "INSERT INTO meta_build VALUES (?, ?)",
            [
                ["dataset", str(version.dataset.public_id)],
                ["version", str(version.number)],
                ["parser_version", str(version.parser_version)],
                ["built_at", timezone.now().isoformat()],
            ],
        )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return summary


def _plan_frame(plan: list[dict[str, Any]]) -> pd.DataFrame:
    """Ряды для запросов сборки: номер ряда таблицы, ключ, вид, пересчёт и его основа."""
    return pd.DataFrame(
        {
            "source": [int(item["source"]) for item in plan],
            "series_key": [item["key"] for item in plan],
            "kind": [item["kind"] for item in plan],
            "derived": [item["derived"] for item in plan],
            "base_key": [item["base_key"] for item in plan],
            "factor": [float(PER_FACTORS.get(item["derived"], 0)) for item in plan],
        }
    )


def _nested_frame() -> pd.DataFrame:
    """Итоги с автономными округами: итог, область без округов, округ, число округов."""
    rows = [
        (total, matching.NESTED_PARENTS[total], member, len(members))
        for total, members in matching.NESTED_MEMBERS.items()
        for member in members
    ]
    return pd.DataFrame(rows, columns=["total", "alone", "member", "members"])


def _population_frame() -> pd.DataFrame:
    """Численность по складу: территория, год, численность."""
    from apps.warehouse.queries.sources import population_table

    rows = [
        (code, int(year), float(value))
        for code, years in population_table().items()
        for year, value in years.items()
    ]
    return pd.DataFrame(rows, columns=["territory_code", "year", "population"])


def _areas_frame() -> pd.DataFrame:
    """Площадь территорий справочника, км²."""
    from apps.catalog.models import Territory

    rows = Territory.objects.exclude(area_km2=None).values_list("code", "area_km2")
    return pd.DataFrame(
        [(code, float(area)) for code, area in rows if area is not None],
        columns=["territory_code", "area"],
    )


def _dimensions(
    connection: duckdb.DuckDBPyConnection, version: DatasetVersion, plan: list[dict[str, Any]]
) -> None:
    """Раздел, показатели, единицы и ряды набора; подписи из таблицы — как есть."""
    connection.execute(
        "INSERT INTO dim_section VALUES (?, ?, ?, NULL, ?, ?)",
        [SECTION_CODE, version.dataset.title, version.dataset.title, 0, len(plan)],
    )
    indicator_codes: dict[str, str] = {}
    units: dict[tuple[str, str], str] = {}
    series_rows = []
    for item in plan:
        indicator_code = indicator_codes.setdefault(
            item["title"], f"u:{version.dataset.code}:i{len(indicator_codes) + 1}"
        )
        unit_code = units.setdefault((item["unit"], item["kind"]), f"u{len(units) + 1}")
        with translation.override("ru"):
            subsection_ru = naming.subsection(item["slices"], item["period"])
        with translation.override("en"):
            subsection_en = naming.subsection(item["slices"], item["period"])
        title = item["title"]
        series_rows.append(
            {
                "series_key": item["key"],
                "indicator_code": indicator_code,
                "section_code": SECTION_CODE,
                "unit_code": unit_code,
                "indicator_name_ru": title,
                "indicator_name_en": None,
                "subsection_ru": subsection_ru or None,
                "subsection_en": subsection_en or None,
                "has_subsection": bool(subsection_ru),
                "full_title_ru": f"{title} — {subsection_ru}" if subsection_ru else title,
                "full_title_en": f"{title} — {subsection_en}" if subsection_en else None,
                "polarity": item["polarity"],
                "source_code": None,
            }
        )
    indicators_frame = pd.DataFrame(
        [(code, title) for title, code in indicator_codes.items()],
        columns=["indicator_code", "name_ru"],
    )
    units_frame = pd.DataFrame(
        [
            (code, unit, "absolute" if kind == indicators.SUM else "rate")
            for (unit, kind), code in units.items()
        ],
        columns=["unit_code", "name", "kind"],
    )
    series_frame = pd.DataFrame(series_rows)
    connection.register("new_indicators", indicators_frame)
    connection.register("new_units", units_frame)
    connection.register("new_series", series_frame)
    connection.execute(
        f"""
        INSERT INTO dim_indicator (indicator_code, name_ru, section_code)
        SELECT indicator_code, name_ru, '{SECTION_CODE}' FROM new_indicators;
        INSERT INTO dim_unit (unit_code, source_name, name_ru, short_name_ru, kind)
        SELECT unit_code, name, name, name, kind FROM new_units;
        INSERT INTO dim_series SELECT * FROM new_series;
        """  # noqa: S608 — в текст подставлена только константа раздела
    )
    for name in ("new_indicators", "new_units", "new_series"):
        connection.unregister(name)


# --- Итоги ------------------------------------------------------------------------------------


def _summary(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Число значений, субъектов, годы и точность по рядам; итог по рядам таблицы."""
    per_series: dict[str, dict[str, Any]] = {}
    for key, valued, regions, first, last, integral, middle in connection.execute(
        SUMMARY_SQL
    ).fetchall():
        per_series[str(key)] = {
            "values": int(valued),
            "regions": int(regions),
            "first_year": first,
            "last_year": last,
            "precision": _precision(integral, middle),
        }
    total = connection.execute(TOTAL_SQL).fetchone() or (0, 0, None, None)
    return {
        "series": per_series,
        "values": int(total[0] or 0),
        "regions": int(total[1] or 0),
        "first_year": total[2],
        "last_year": total[3],
    }


def _precision(integral: bool | None, middle: float | None) -> int:
    """Знаков после запятой: у целых — ноль, иначе по середине ряда, как у рядов склада."""
    if integral or middle is None:
        return 0
    if middle >= LARGE_VALUE_THRESHOLD:
        return 0
    return 1 if middle >= SMALL_VALUE_THRESHOLD else 2


def _save(
    version: DatasetVersion, plan: list[dict[str, Any]], summary: dict[str, Any], file_name: str
) -> None:
    """Ряды версии, итоги сборки и новый файл; прежние файлы сборки удаляются."""
    records = []
    for item in plan:
        found = summary["series"].get(item["key"], {})
        records.append(
            DatasetSeries(
                version=version,
                code=item["code"],
                indicator=item["indicator"][:300],
                title=item["title"][:300],
                unit=item["unit"][:120],
                kind=item["kind"],
                polarity=item["polarity"],
                precision=found.get("precision", 1),
                slices=[list(pair) for pair in item["slices"]],
                period=item["period"],
                derived=item["derived"],
                base_code=item["base_code"],
                order=item["order"],
                values_count=found.get("values", 0),
                regions_count=found.get("regions", 0),
                first_year=found.get("first_year"),
                last_year=found.get("last_year"),
            )
        )
    previous = version.data_file
    with transaction.atomic():
        DatasetSeries.objects.filter(version=version).delete()
        DatasetSeries.objects.bulk_create(records)
        version.refresh_from_db(fields=["report"])
        version.report = {
            **version.report,
            "build": {"alone_missing": summary["alone_missing"]},
        }
        version.data_file = file_name
        version.state = DatasetVersion.State.BUILT
        version.error = ""
        version.values_count = summary["values"]
        version.series_count = len(records)
        version.regions_count = summary["regions"]
        version.first_year = summary["first_year"]
        version.last_year = summary["last_year"]
        version.save()
        Dataset.objects.filter(pk=version.dataset_id).update(state=Dataset.State.READY)
    if previous and previous != file_name:
        _remove(version.directory / previous)
    from .services import update_size

    update_size(version.dataset)


def _remove(path: Path) -> None:
    """Удалить файл сборки; занятый другим процессом (Windows) удалит очистка позже."""
    close_dataset(path)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.info("userdata: файл %s занят, удалится при очистке", path.name)
