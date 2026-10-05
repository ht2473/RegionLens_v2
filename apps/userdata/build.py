"""
Сборка набора: извлечённые наблюдения и описание показателей → файл DuckDB со схемой склада.

Схема — та же, что у склада (``apps/warehouse/sql/schema.sql``), поэтому выборки холста
и витрины (``marts.build_*``) работают на файле набора без изменений. В файл попадают
территории справочника, ряды таблицы, область без автономных округов (для сумм — итог
за вычетом округов, с пометкой «рассчитано»), пересчёты сумм на жителей и на км²
и строки вне справочника — отдельной таблицей: на карту и в рейтинг они не попадают.
Всё считается в DuckDB одним проходом по всем рядам. Файл неизменяем: новая сборка
пишет новый файл.

Значения рядов таблицы каждой версии — выпуски ``fact_vintage`` и ``dim_edition``, как
сборники в складе: новая версия переносит выпуски прежних из их файла и добавляет свой,
витрина пересмотров (``mart_revision``) и отчёт о различиях с прежней версией строятся
на них.
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
from django.utils.translation import gettext as _

from apps.core.templatetags.formatting import LARGE_VALUE_THRESHOLD, SMALL_VALUE_THRESHOLD
from apps.warehouse import routing
from apps.warehouse.duckdb_client import DataSource, close_dataset, dataset_connection
from apps.warehouse.etl import marts
from apps.warehouse.etl.dimensions import load_territories
from apps.warehouse.etl.facts import build_revisions
from apps.warehouse.queries import COUNTRY_CODE

from . import (
    extract,
    formula_store,
    formulas,
    indicators,
    jobs,
    matching,
    monthly,
    naming,
    recognize,
)
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
# Пересчёты (этап Б): суффикс кода, приписка к названию и единица; пустая единица — как
# у исходного ряда. Названия хранятся как в таблице — по-русски.
RECALC_SUFFIXES = {
    indicators.RECALC_REAL: "-real",
    indicators.RECALC_RUSSIA: "-ru100",
    indicators.RECALC_GROWTH: "-yoy",
    indicators.RECALC_SHARE: "-share",
}
RECALC_TITLES = {
    indicators.RECALC_REAL: "{title} в ценах последнего года",
    indicators.RECALC_RUSSIA: "{title}, Россия = 100",
    indicators.RECALC_GROWTH: "{title}, % к предыдущему году",
    indicators.RECALC_SHARE: "{title}: доля в сумме по субъектам",
}
RECALC_UNITS = {
    indicators.RECALC_RUSSIA: "Россия = 100",
    indicators.RECALC_GROWTH: "% к предыдущему году",
    indicators.RECALC_SHARE: "%",
}
# Свёртка месяцев в год: способ сложения, приписка к названию.
MONTHS_TITLES = {
    indicators.MONTHS_SUM: "{title} за год (сумма месяцев)",
    indicators.MONTHS_MEAN: "{title} за год (среднее за месяц)",
    indicators.MONTHS_DECEMBER: "{title} на конец года (декабрь)",
    indicators.MONTHS_YTD: "{title} за год (январь–декабрь)",
}
SLICE_SUM_VALUE = "сумма по разрезу «{header}»"
# Ряд индекса потребительских цен (% к предыдущему году) — у рядов склада в ценах года.
CPI_METHOD = "prices"
# Больше производных рядов не собирается: пересчёты умножают ряды таблицы.
MAX_DERIVED = 1500
# Сумма по разрезу — у двух значений и больше.
MIN_FOLD_MEMBERS = 2
REAL_YEARS_SQL = "SELECT derived_key, base_year FROM real_target ORDER BY 1"
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

    CREATE TEMP TABLE alone AS
    WITH totals AS (
        SELECT f.series_key, n.total, n.alone, n.members, f.year, f.value
        FROM facts AS f
        JOIN plan AS p USING (series_key)
        JOIN (SELECT DISTINCT total, alone, members FROM nested) AS n
          ON f.territory_code = n.total
        WHERE p.kind = 'sum' AND p.derived = '' AND f.value IS NOT NULL
    ),
    parts AS (
        SELECT f.series_key, n.total, f.year, sum(f.value) AS value,
               count(f.value) AS filled, min(f.value) AS lowest
        FROM facts AS f
        JOIN nested AS n ON f.territory_code = n.member
        GROUP BY 1, 2, 3
    )
    SELECT t.series_key, t.alone, t.year, t.value - p.value AS value,
           -- Итог меньше суммы округов при неотрицательных значениях: в таблице область
           -- уже без округов, вычитать нечего.
           t.value >= 0 AND p.lowest >= 0 AND t.value < p.value AS impossible
    FROM totals AS t
    JOIN parts  AS p USING (series_key, total, year)
    WHERE p.filled = t.members
      AND NOT EXISTS (
          SELECT 1 FROM facts AS x
          WHERE x.series_key = t.series_key AND x.territory_code = t.alone
      )
    ORDER BY 1, 2, 3;

    INSERT INTO facts
    SELECT series_key, alone, year, value, 0, 2 FROM alone WHERE NOT impossible
"""

# Области, у которых итог с округами меньше суммы округов: «итог» в таблице — область
# без округов, ответ о вложенных по умолчанию не подошёл.
ALONE_CONFLICT_SQL = "SELECT DISTINCT alone FROM alone WHERE impossible ORDER BY 1"

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

# Свёртки: сумма по разрезу и месяцы в год — по всем рядам-слагаемым, только когда есть
# значения всех; одно слагаемое (декабрь, январь–декабрь) — значение как есть.
FOLD_SQL = """
    INSERT INTO facts
    SELECT m.series_key, f.territory_code, f.year,
           CASE WHEN any_value(m.method) = 'mean' THEN avg(f.value) ELSE sum(f.value) END,
           0,
           CASE WHEN any_value(m.members) > 1 THEN 2 ELSE 0 END
    FROM folds AS m
    JOIN facts AS f ON f.series_key = m.member_key
    WHERE f.value IS NOT NULL
    GROUP BY m.series_key, f.territory_code, f.year
    HAVING count(*) = any_value(m.members)
"""

# В ценах последнего года: значение года t, умноженное на индексы цен региона за годы
# t+1 … T (T — последний год ряда, не позже последнего года индексов). Цепочка индексов
# без пропусков: разность номеров лет в цепочке равна разности самих лет.
REAL_TARGET_SQL = """
    CREATE TEMP TABLE real_target AS
    SELECT d.series_key AS derived_key, d.base_key,
           least(max(f.year), (SELECT max(year) FROM cpi)) AS base_year
    FROM plan AS d
    JOIN facts AS f ON f.series_key = d.base_key
    WHERE d.derived = 'real' AND f.value IS NOT NULL
    GROUP BY 1, 2
    ORDER BY 1
"""

REAL_SQL = """
    INSERT INTO facts
    WITH chain AS (
        SELECT territory_code, year,
               sum(ln(cpi / 100)) OVER w AS log_chain,
               count(*) OVER w AS position
        FROM cpi
        WHERE cpi > 0
        WINDOW w AS (PARTITION BY territory_code ORDER BY year)
    )
    SELECT t.derived_key, f.territory_code, f.year,
           f.value * exp(b.log_chain - c.log_chain), 0, f.flags | 2
    FROM real_target AS t
    JOIN facts AS f ON f.series_key = t.base_key
    JOIN chain AS c ON c.territory_code = f.territory_code AND c.year = f.year
    JOIN chain AS b ON b.territory_code = f.territory_code AND b.year = t.base_year
    WHERE f.value IS NOT NULL AND b.position - c.position = t.base_year - f.year
"""

# Сумма по субъектам за год и наибольшее число субъектов ряда — для «России = 100» у сумм
# без строки страны и для доли в сумме.
SUBJECTS_SQL = """
    CREATE TEMP TABLE subjects AS
    SELECT f.series_key, f.year, sum(f.value) AS value, count(f.value) AS filled
    FROM facts AS f
    JOIN dim_territory AS t USING (territory_code)
    WHERE t.level = 'region' AND NOT t.is_aggregate AND f.value IS NOT NULL
    GROUP BY 1, 2
    ORDER BY 1, 2
"""

# Россия = 100: значение страны из таблицы; у суммы без строки страны — сумма субъектов
# за год, в котором есть все субъекты ряда.
RUSSIA_SQL = """
    INSERT INTO facts
    WITH fullest AS (
        SELECT series_key, max(filled) AS most FROM subjects GROUP BY 1
    ),
    totals AS (
        SELECT d.series_key AS derived_key, d.base_key, s.year,
               coalesce(
                   c.value,
                   CASE WHEN d.base_kind = 'sum' AND s.filled = u.most THEN s.value END
               ) AS total
        FROM plan AS d
        JOIN subjects AS s ON s.series_key = d.base_key
        JOIN fullest AS u ON u.series_key = d.base_key
        LEFT JOIN facts AS c
          ON c.series_key = d.base_key AND c.year = s.year
         AND c.territory_code = $country AND c.value IS NOT NULL
        WHERE d.derived = 'russia'
    )
    SELECT t.derived_key, f.territory_code, f.year, f.value / t.total * 100, 0, f.flags | 2
    FROM totals AS t
    JOIN facts AS f ON f.series_key = t.base_key AND f.year = t.year
    WHERE f.value IS NOT NULL AND t.total IS NOT NULL AND t.total <> 0
"""

# Темп к прошлому году (к тому же периоду прошлого года у рядов «январь–июль»): только
# у положительных значений.
GROWTH_SQL = """
    INSERT INTO facts
    SELECT d.series_key, f.territory_code, f.year, f.value / p.value * 100, 0, f.flags | 2
    FROM plan AS d
    JOIN facts AS f ON f.series_key = d.base_key
    JOIN facts AS p
      ON p.series_key = d.base_key AND p.territory_code = f.territory_code
     AND p.year = f.year - 1
    WHERE d.derived = 'growth' AND f.value >= 0 AND p.value > 0
"""

# Доля в сумме по субъектам за тот же год.
SHARE_SQL = """
    INSERT INTO facts
    SELECT d.series_key, f.territory_code, f.year, f.value / s.value * 100, 0, f.flags | 2
    FROM plan AS d
    JOIN facts AS f ON f.series_key = d.base_key
    JOIN subjects AS s ON s.series_key = d.base_key AND s.year = f.year
    WHERE d.derived = 'share' AND f.value IS NOT NULL AND s.value <> 0
"""

# Значения рядов с периодом внутри года — в слой месяцев под ключом группы показателя.
MONTHS_SQL = """
    INSERT INTO fact_month
    SELECT p.month_key, f.territory_code, CAST(f.year AS SMALLINT), CAST(p.month AS TINYINT),
           p.month_kind, f.value, CAST(f.quality AS TINYINT), CAST(f.flags AS TINYINT), 'user'
    FROM facts AS f
    JOIN plan AS p USING (series_key)
    JOIN dim_territory AS t USING (territory_code)
    WHERE p.month_key <> ''
    ORDER BY 1, 2, 3, 4
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

# Выпуск версии: значения рядов из таблицы (без пересчётов и формул — они следуют за ними).
VINTAGE_SQL = """
    INSERT INTO fact_vintage
    SELECT f.series_key, f.territory_code, CAST(f.year AS SMALLINT), $edition,
           CAST($edition_year AS SMALLINT), f.value, CAST(f.quality AS TINYINT),
           CAST(f.flags AS TINYINT)
    FROM facts AS f
    JOIN plan AS p USING (series_key)
    WHERE p.derived = ''
    ORDER BY 1, 2, 3
"""
# Выпуски прежних версий из файла прежней сборки (своей при пересборке).
HISTORY_VINTAGE_SQL = """
    SELECT v.* FROM fact_vintage AS v
    JOIN dim_edition AS e USING (edition_code)
    WHERE e.source_code = 'version' AND e.edition_rank < ?
    ORDER BY v.series_key, v.territory_code, v.year, v.edition_code
"""
HISTORY_EDITIONS_SQL = """
    SELECT * FROM dim_edition
    WHERE source_code = 'version' AND edition_rank < ?
    ORDER BY edition_rank
"""
# Различия с прежней версией: изменённые, появившиеся и пропавшие значения.
CHANGES_SQL = """
    WITH before AS (
        SELECT series_key, territory_code, year, value
        FROM fact_vintage WHERE edition_code = $base
    ),
    after AS (
        SELECT series_key, territory_code, year, value
        FROM fact_vintage WHERE edition_code = $edition
    ),
    pairs AS (
        SELECT b.value AS old, a.value AS new
        FROM before AS b
        FULL JOIN after AS a USING (series_key, territory_code, year)
    )
    SELECT
        count(*) FILTER (WHERE old IS NOT NULL AND new IS NOT NULL AND old <> new),
        count(*) FILTER (WHERE old IS NULL AND new IS NOT NULL),
        count(*) FILTER (WHERE old IS NOT NULL AND new IS NULL),
        median(abs((new - old) / old)) FILTER (
            WHERE old IS NOT NULL AND new IS NOT NULL AND old <> new AND old <> 0
        ),
        count(*) FILTER (
            WHERE old IS NOT NULL AND new IS NOT NULL AND old <> 0
              AND abs((new - old) / old) >= 0.05
        )
    FROM pairs
"""
# Территории и годы со значениями в выпуске; столбец — из двух имён схемы.
PRESENT_SQL = {
    column: f"""
        SELECT DISTINCT {column} FROM fact_vintage
        WHERE edition_code = ? AND value IS NOT NULL
        ORDER BY 1
    """  # noqa: S608 — имя столбца — константа модуля, значения — параметрами
    for column in ("territory_code", "year")
}

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
    """
    Ряды набора с описанием показателя: из таблицы, свёртки (сумма по разрезу, месяцы
    в год) и пересчёты каждого из них (на жителей и км², в ценах года, Россия = 100, темп,
    доля). Ряды показателя идут подряд, пересчёт — сразу за своим рядом.
    """
    described = {item.name: item for item in indicators.indicators_of(version)}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(report["series"]):
        indicator = described[item["indicator"]]
        grouped.setdefault(indicator.name, []).append(
            {
                "source": index,
                "code": item["code"],
                "indicator": item["indicator"],
                "title": indicator.title,
                "unit": indicator.unit or item["unit"],
                "kind": indicator.kind,
                "base_kind": indicator.kind,
                "polarity": indicator.polarity,
                "slices": [tuple(pair) for pair in item["slices"]],
                "period": item["period"],
                "derived": "",
                "base_code": "",
                "members": [],
                "method": "",
            }
        )
    plan: list[dict[str, Any]] = []
    for name, bases in grouped.items():
        indicator = described[name]
        for primary in [*bases, *_folds(indicator, bases)]:
            plan.append(primary)
            plan.extend(_recalculations(indicator, primary))
    # Показатели по формулам — последними: они ссылаются на ряды выше.
    plan.extend(formula_store.plan_items(version))
    derived = sum(1 for item in plan if item["derived"])
    if derived > MAX_DERIVED:
        raise extract.ExtractError(
            _(
                "Пересчётов и свёрток получается %(count)s — больше %(limit)s. Отметьте их "
                "только у нужных показателей."
            )
            % {"count": derived, "limit": MAX_DERIVED}
        )
    prefix = f"u:{version.dataset.code}:"
    for order, item in enumerate(plan):
        item.update(monthly.plan_fields(item))
        item["order"] = order
        item["key"] = prefix + item["code"]
        item["base_key"] = prefix + item["base_code"] if item["base_code"] else ""
        item["member_keys"] = [prefix + code for code in item["members"]]
        item["month_key"] = prefix + item["month_code"] if item["month_code"] else ""
    return plan


def _folds(indicator: indicators.Indicator, bases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Свёртки показателя: сумма по отмеченным разрезам и месяцы в год."""
    folds: list[dict[str, Any]] = []
    for header in indicator.fold_slices:
        siblings = {dict(item["slices"]).get(header, "") for item in bases}
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for item in bases:
            values = dict(item["slices"])
            # Итог разреза («Всего», «Оба пола») к сумме не добавляется: он её повторил бы.
            if header not in values or recognize.is_total(values[header], siblings):
                continue
            others = tuple(pair for pair in item["slices"] if pair[0] != header)
            groups.setdefault((others, item["period"]), []).append(item)
        for (others, period), members in groups.items():
            if len(members) < MIN_FOLD_MEMBERS:
                continue
            folds.append(
                {
                    **members[0],
                    "source": -1,
                    "code": naming.series_code(indicator.name, others, period) + "-sum",
                    "slices": [*others, (header, SLICE_SUM_VALUE.format(header=header))],
                    "derived": DatasetSeries.Derived.SLICE_SUM.value,
                    "members": [member["code"] for member in members],
                    "method": indicators.MONTHS_SUM,
                }
            )
    if indicator.months:
        folds.extend(_month_folds(indicator, bases))
    return folds


def _month_folds(
    indicator: indicators.Indicator, bases: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Месяцы в год: по ряду на набор значений разрезов; слагаемые — ряды месяцев."""
    method = indicator.months
    months = [f"month:{number}" for number in range(1, 13)]
    wanted = {
        indicators.MONTHS_SUM: months,
        indicators.MONTHS_MEAN: months,
        indicators.MONTHS_DECEMBER: ["month:12"],
        indicators.MONTHS_YTD: ["ytd:12"],
    }[method]
    groups: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for item in bases:
        if item["period"] in wanted:
            groups.setdefault(tuple(item["slices"]), {})[item["period"]] = item
    folds = []
    for slices, by_period in groups.items():
        if any(period not in by_period for period in wanted):
            continue
        folds.append(
            {
                **by_period[wanted[0]],
                "source": -1,
                "code": naming.series_code(indicator.name, slices, indicators.YEAR_PERIOD) + "-y",
                "title": MONTHS_TITLES[method].format(title=indicator.title),
                "period": indicators.YEAR_PERIOD,
                "derived": DatasetSeries.Derived.MONTHS.value,
                "members": [by_period[period]["code"] for period in wanted],
                "method": indicators.MONTHS_MEAN
                if method == indicators.MONTHS_MEAN
                else indicators.MONTHS_SUM,
            }
        )
    return folds


def _recalculations(
    indicator: indicators.Indicator, primary: dict[str, Any]
) -> list[dict[str, Any]]:
    """Пересчёты ряда таблицы или свёртки: на жителей и км² (суммы) и отмеченные в описании."""
    common = {
        "source": -1,
        "members": [],
        "method": "",
        "base_code": primary["code"],
        "base_kind": primary["kind"],
    }
    found = []
    if indicator.kind == indicators.SUM:
        for per in indicator.per:
            found.append(
                {
                    **primary,
                    **common,
                    "code": primary["code"] + PER_SUFFIXES[per],
                    "title": f"{primary['title']} {_per_title(per)}",
                    "kind": indicators.RELATIVE,
                    "derived": per,
                }
            )
    for recalc in indicator.recalc:
        # Индексы цен годовые: в цены года переводятся только годовые значения.
        if recalc == indicators.RECALC_REAL and primary["period"] != indicators.YEAR_PERIOD:
            continue
        found.append(
            {
                **primary,
                **common,
                "code": primary["code"] + RECALC_SUFFIXES[recalc],
                "title": RECALC_TITLES[recalc].format(title=primary["title"]),
                "unit": RECALC_UNITS.get(recalc, primary["unit"]),
                "kind": primary["kind"]
                if recalc == indicators.RECALC_REAL
                else indicators.RELATIVE,
                "derived": recalc,
            }
        )
    return found


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
        connection.register("raw", raw)
        connection.register("plan", _plan_frame(plan))
        connection.register("nested", _nested_frame())
        connection.execute(FACTS_SQL)
        alone_missing = [row[0] for row in connection.execute(ALONE_MISSING_SQL).fetchall()]
        alone_conflict = [row[0] for row in connection.execute(ALONE_CONFLICT_SQL).fetchall()]
        _derive(connection, plan)
        formula_errors = _formulas(connection, version, plan)
        _dimensions(connection, version, plan)
        connection.execute(OBSERVATIONS_SQL)
        changes = _vintage(connection, version, plan)
        _months(connection, version)
        connection.execute(OUTSIDE_SQL)
        marts.build_coverage(connection)
        marts.build_stats(connection)
        marts.build_ranks(connection)
        summary = _summary(connection)
        summary["alone_missing"] = alone_missing
        summary["alone_conflict"] = alone_conflict
        summary["formula_errors"] = formula_errors
        summary["changes"] = changes
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


def _derive(connection: duckdb.DuckDBPyConnection, plan: list[dict[str, Any]]) -> None:
    """
    Свёртки, затем пересчёты рядов таблицы и свёрток. Название ряда «в ценах года»
    получает свой год — последний год ряда, за который есть индексы цен.
    """
    derived = {item["derived"] for item in plan if item["derived"]}
    folds = _folds_frame(plan)
    if not folds.empty:
        connection.register("folds", folds)
        connection.execute(FOLD_SQL)
    if derived & set(PER_FACTORS):
        connection.register("population", _population_frame())
        connection.execute(PER_CAPITA_SQL)
    if DatasetSeries.Derived.PER_KM2.value in derived:
        connection.register("areas", _areas_frame())
        connection.execute(PER_AREA_SQL)
    if indicators.RECALC_REAL in derived:
        connection.register("cpi", _cpi_frame(connection))
        connection.execute(REAL_TARGET_SQL)
        years = dict(connection.execute(REAL_YEARS_SQL).fetchall())
        for item in plan:
            if item["derived"] == indicators.RECALC_REAL and years.get(item["key"]):
                base = item["title"].removesuffix(" в ценах последнего года")
                item["title"] = f"{base} в ценах {int(years[item['key']])} года"
        connection.execute(REAL_SQL)
    if derived & {indicators.RECALC_RUSSIA, indicators.RECALC_SHARE}:
        connection.execute(SUBJECTS_SQL)
        if indicators.RECALC_RUSSIA in derived:
            connection.execute(RUSSIA_SQL, {"country": COUNTRY_CODE})
        if indicators.RECALC_SHARE in derived:
            connection.execute(SHARE_SQL)
    if indicators.RECALC_GROWTH in derived:
        connection.execute(GROWTH_SQL)


def _formulas(
    connection: duckdb.DuckDBPyConnection, version: DatasetVersion, plan: list[dict[str, Any]]
) -> dict[str, str]:
    """
    Показатели по формулам — по порядку: формула видит ряды таблицы, их пересчёты и формулы
    выше. Ряды других таблиц — только того же владельца; формула, которую не посчитать,
    остаётся без значений, причина — в отчёте сборки.
    """
    items = [item for item in plan if item["derived"] == DatasetSeries.Derived.FORMULA]
    if not items:
        return {}
    own_keys = {item["key"] for item in plan}
    territories = {
        str(code): bool(subject)
        for code, subject in connection.execute(
            "SELECT territory_code, level = 'region' AND NOT is_aggregate FROM dim_territory "
            "ORDER BY 1"
        ).fetchall()
    }
    errors: dict[str, str] = {}
    for item in items:
        try:
            tree = formulas.parse(item["expression"])
            keys = formulas.keys_of(item["expression"])
            inputs = _formula_inputs(connection, version, keys, own_keys)
            missing = [key for key in keys if key not in inputs]
            if missing:
                names = ", ".join(f"«{item['labels'].get(key, key)}»" for key in missing)
                raise formulas.FormulaError(
                    _("Нет значений показателей: %(names)s.") % {"names": names}
                )
            outcome = formulas.evaluate(tree, inputs, territories)
        except formulas.FormulaError as error:
            errors[item["code"]] = str(error)
            continue
        connection.register(
            "formula_rows",
            pd.DataFrame(outcome.rows, columns=["territory_code", "year", "value"]),
        )
        connection.execute(
            "INSERT INTO facts SELECT ?, territory_code, CAST(year AS INTEGER), value, 0, 2 "
            "FROM formula_rows ORDER BY territory_code, year",
            [item["key"]],
        )
        connection.unregister("formula_rows")
        if outcome.warnings:
            errors[item["code"]] = " ".join(outcome.warnings)
    return errors


def _formula_inputs(
    connection: duckdb.DuckDBPyConnection,
    version: DatasetVersion,
    keys: list[str],
    own_keys: set[str],
) -> dict[str, formula_store.Rows]:
    """Значения рядов формулы: этой сборки, файлов других своих таблиц и склада."""
    inputs: dict[str, formula_store.Rows] = {}
    own = [key for key in keys if key in own_keys]
    if own:
        for key, code, year, value in connection.execute(
            "SELECT series_key, territory_code, year, value FROM facts "
            "WHERE series_key IN (SELECT unnest(?)) ORDER BY 1, 2, 3",
            [own],
        ).fetchall():
            inputs.setdefault(str(key), []).append((str(code), int(year), value))
    others: dict[str, list[str]] = {}
    for key in keys:
        if key not in own_keys and routing.is_user_key(key):
            others.setdefault(routing.dataset_code(key), []).append(key)
    if others:
        dataset = version.dataset
        for table in (
            formula_store.siblings(dataset)
            .filter(code__in=list(others))
            .select_related("current_version")
        ):
            inputs.update(formula_store.table_rows(table.current_version, others[table.code]))
    official = [key for key in keys if key not in own_keys and not routing.is_user_key(key)]
    inputs.update(formula_store.official_rows(official))
    return inputs


def _vintage(
    connection: duckdb.DuckDBPyConnection, version: DatasetVersion, plan: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """
    Выпуск этой версии и выпуски прежних, витрина пересмотров; вернуть различия с прежней
    версией (или ``None`` у первой).
    """
    edition = edition_code(version.number)
    _history(connection, version)
    count, first, last = connection.execute(
        "SELECT count(*), min(year), max(year) FROM facts AS f JOIN plan AS p USING (series_key) "
        "WHERE p.derived = '' AND f.value IS NOT NULL"
    ).fetchone() or (0, None, None)
    uploaded = timezone.localtime(version.created_at)
    connection.execute(
        "INSERT INTO dim_edition VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 'version', ?)",
        [
            edition,
            version.dataset.title,
            version.file_name[:200],
            uploaded.year,
            int(count or 0),
            first,
            last,
            f"{version.number} · {uploaded:%d.%m.%Y}",
            uploaded.date(),
            version.number,
        ],
    )
    connection.execute(VINTAGE_SQL, {"edition": edition, "edition_year": uploaded.year})
    build_revisions(connection)
    previous = version.previous
    if previous is None:
        return None
    return _changes(connection, version, previous, plan)


def _changes(
    connection: duckdb.DuckDBPyConnection,
    version: DatasetVersion,
    previous: DatasetVersion,
    plan: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Различия выпуска версии с выпуском прежней: значения, ряды, территории и годы."""
    base = edition_code(previous.number)
    edition = edition_code(version.number)
    known = connection.execute(
        "SELECT count(*) FROM dim_edition WHERE edition_code = ?", [base]
    ).fetchone()
    if not known or not known[0]:
        return None
    changed, added, removed, median, large = connection.execute(
        CHANGES_SQL, {"base": base, "edition": edition}
    ).fetchone() or (0, 0, 0, None, 0)
    primary = {item["code"]: item for item in plan if not item["derived"]}
    before = {
        record.code: record
        for record in previous.series.filter(derived="").only("code", "title", "slices", "period")
    }

    def present(column: str, code: str) -> set[Any]:
        return {row[0] for row in connection.execute(PRESENT_SQL[column], [code]).fetchall()}

    territories = present("territory_code", base), present("territory_code", edition)
    years = present("year", base), present("year", edition)
    return {
        "base": previous.number,
        "changed": int(changed or 0),
        "added": int(added or 0),
        "removed": int(removed or 0),
        "median": float(median) if median is not None else None,
        "large": int(large or 0),
        "new_series": [
            naming.full_title(item["title"], item["slices"], item["period"])
            for code, item in primary.items()
            if code not in before
        ],
        "gone_series": [
            naming.full_title(record.title, record.slices, record.period)
            for code, record in before.items()
            if code not in primary
        ],
        "new_territories": sorted(territories[1] - territories[0]),
        "gone_territories": sorted(territories[0] - territories[1]),
        "new_years": sorted(int(year) for year in years[1] - years[0]),
        "gone_years": sorted(int(year) for year in years[0] - years[1]),
    }


def edition_code(number: int) -> str:
    """Выпуск версии таблицы в ``fact_vintage``."""
    return f"v{number}"


def _history(connection: duckdb.DuckDBPyConnection, version: DatasetVersion) -> None:
    """
    Перенести выпуски прежних версий: из своего прежнего файла сборки (пересборка) или из
    файла версии, на которой основана эта. Файлы читаются через соединения слоя рядов:
    второе соединение с открытым процессом файлом DuckDB не открывается.
    """
    candidates = [version, version.previous] if version.previous_id else [version]
    for owner in candidates:
        if owner is None or not owner.data_file or owner.data_path is None:
            continue
        if not owner.data_path.exists():
            continue
        source = DataSource(path=owner.data_path, generation=f"u{owner.pk}-{owner.data_file}")
        reader = dataset_connection(source)
        rows = reader.execute(HISTORY_VINTAGE_SQL, [version.number]).to_arrow_table()
        editions = reader.execute(HISTORY_EDITIONS_SQL, [version.number]).to_arrow_table()
        connection.register("history_vintage", rows)
        connection.register("history_editions", editions)
        connection.execute("INSERT INTO fact_vintage SELECT * FROM history_vintage")
        connection.execute("INSERT INTO dim_edition SELECT * FROM history_editions")
        connection.unregister("history_vintage")
        connection.unregister("history_editions")
        return


def _months(connection: duckdb.DuckDBPyConnection, version: DatasetVersion) -> None:
    """Слой месяцев для вида «По месяцам»: значения рядов периодов внутри года."""
    connection.execute(MONTHS_SQL)
    count, first, last = connection.execute(
        "SELECT count(*), min(year), max(year) FROM fact_month"
    ).fetchone() or (0, None, None)
    if not count:
        return
    connection.execute(
        "INSERT INTO dim_edition VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, NULL, 0)",
        [
            monthly.EDITION,
            version.dataset.title,
            version.dataset.title,
            int(last),
            int(count),
            int(first),
            int(last),
            version.file_name[:100],
        ],
    )


def _plan_frame(plan: list[dict[str, Any]]) -> pd.DataFrame:
    """Ряды для запросов сборки: номер ряда таблицы, ключ, вид, пересчёт и его основа."""
    return pd.DataFrame(
        {
            "source": [int(item["source"]) for item in plan],
            "series_key": [item["key"] for item in plan],
            "kind": [item["kind"] for item in plan],
            "base_kind": [item.get("base_kind", item["kind"]) for item in plan],
            "derived": [item["derived"] for item in plan],
            "base_key": [item["base_key"] for item in plan],
            "factor": [float(PER_FACTORS.get(item["derived"], 0)) for item in plan],
            "month_key": [item.get("month_key", "") for item in plan],
            "month": [int(item.get("month", 0)) for item in plan],
            "month_kind": [item.get("month_kind", "") for item in plan],
        }
    )


def _folds_frame(plan: list[dict[str, Any]]) -> pd.DataFrame:
    """Слагаемые свёрток: ряд свёртки, ряд-слагаемое, число слагаемых и способ."""
    rows = [
        (item["key"], member, len(item["member_keys"]), item["method"])
        for item in plan
        for member in item["member_keys"]
    ]
    return pd.DataFrame(rows, columns=["series_key", "member_key", "members", "method"])


def _cpi_frame(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    Индекс потребительских цен (% к прошлому году) по территориям таблицы — тот же ряд,
    по которому сайт переводит в цены года денежные показатели основного набора.
    """
    from apps.warehouse.queries import featured_series, series_values

    key = next(
        (
            item.real.index
            for item in featured_series()
            if item.real is not None and item.real.method == CPI_METHOD
        ),
        None,
    )
    codes = [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT territory_code FROM facts ORDER BY 1"
        ).fetchall()
    ]
    values = series_values([key], codes).get(key, {}) if key else {}
    rows = [
        (code, int(year), float(value))
        for code, years in values.items()
        for year, value in years.items()
        if value is not None
    ]
    return pd.DataFrame(rows, columns=["territory_code", "year", "cpi"])


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
            "build": {
                "alone_missing": summary["alone_missing"],
                "alone_conflict": summary["alone_conflict"],
                "formula_errors": summary["formula_errors"],
            },
        }
        if summary["changes"] is not None:
            version.report["changes"] = summary["changes"]
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
    # Собранная новая версия становится текущей; лишние старые версии уходят.
    from .renew import promote

    promote(version)
    from .services import update_size

    update_size(version.dataset)
    # Таблицы, формулы которых ссылаются на эту, считаются по её новым значениям.
    formula_store.rebuild_dependents(version.dataset)


def _remove(path: Path) -> None:
    """Удалить файл сборки; занятый другим процессом (Windows) удалит очистка позже."""
    close_dataset(path)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.info("userdata: файл %s занят, удалится при очистке", path.name)
