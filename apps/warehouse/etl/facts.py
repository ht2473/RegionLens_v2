"""
Загрузка фактов: версии наблюдений по выпускам, каноническая таблица и пересмотры.

Заглушки источника становятся ``NULL`` с признаком качества; из версий побеждает самое
позднее непропущенное значение (правило источника); значение по России приводится
к единице территорий, иначе оно в тысячу раз меньше суммы регионов. Доказанные ошибки
набора исправляются по файлу поправок до выбора значения.
"""

from __future__ import annotations

import json
import logging

import duckdb
from django.conf import settings

from apps.catalog.constants import SENTINEL_HIDDEN, SENTINEL_NO_DATA, ValueFlag, ValueQuality
from apps.warehouse.duckdb_client import require_row

logger = logging.getLogger(__name__)

# Поправки к исходному набору в справочниках проекта.
CORRECTIONS_FILE = "source_corrections.json"

# Категории единиц для SQL: у уровня значение зависит от множителя, у относительной — нет.
LEVEL_KINDS = ", ".join(f"'{kind}'" for kind in ("currency", "physical", "absolute"))
RELATIVE_KINDS = ", ".join(f"'{kind}'" for kind in ("share", "index", "rate"))


def load_vintages(connection: duckdb.DuckDBPyConnection) -> int:
    """
    Заполнить таблицу версий наблюдений — по строке на выпуск издания.

    Повторы внутри выпуска сводятся с приоритетом непропущенного значения.
    """
    connection.execute(
        f"""
        INSERT INTO fact_vintage
        SELECT
            m.series_key,
            t.territory_code,
            -- «На 31 декабря» и «на 1 января» следующего года — одна дата: строка выпуска
            -- с другим моментом наблюдения ставится в год, принятый в ряду.
            CAST(s.year + CASE
                    WHEN su.moment = 'jan1' AND us.moment = 'dec31' THEN 1
                    WHEN su.moment = 'dec31' AND us.moment = 'jan1' THEN -1
                    ELSE 0
                END AS SMALLINT)                        AS year,
            e.edition_code,
            CAST(e.edition_year AS SMALLINT)            AS edition_year,
            -- Значение сохраняется только для действительных наблюдений и приводится
            -- к единице измерения территорий: для страны в целом источник может
            -- объявлять укрупнённую единицу, и без приведения сумма регионов
            -- расходится с итогом по России в тысячу раз. Строка в той же единице
            -- с другим множителем («человек» при ряде в тысячах) приводится к ряду.
            max(CASE
                    WHEN s.indicator_value IN ({SENTINEL_NO_DATA}, {SENTINEL_HIDDEN}) THEN NULL
                    ELSE s.indicator_value
                        * CASE WHEN t.level = 'country'
                               THEN coalesce(m.country_scale, us.country_scale, 1)
                               ELSE 1 END
                        * CASE WHEN us.unit_kind IN ({LEVEL_KINDS})
                                AND us.unit_kind = su.unit_kind
                                AND us.base_label <> ''
                                AND us.base_label = su.base_label
                               THEN coalesce(us.multiplier, 1) / coalesce(su.multiplier, 1)
                               ELSE 1 END
                END)                                    AS value,
            -- Признак качества: наименьший код соответствует наиболее полному сведению.
            CAST(min(CASE
                    WHEN s.indicator_value = {SENTINEL_NO_DATA} THEN {ValueQuality.NO_DATA}
                    WHEN s.indicator_value = {SENTINEL_HIDDEN}  THEN {ValueQuality.HIDDEN}
                    ELSE {ValueQuality.OBSERVED}
                END) AS TINYINT)                        AS quality,
            CAST(0 AS TINYINT)                          AS flags
        FROM src           AS s
        JOIN dim_territory AS t ON t.source_name = s.object_name
        JOIN dim_edition   AS e ON e.source_name = s.source
        JOIN map_series    AS m
              ON m.indicator_code = s.indicator_code
             AND m.subsection_raw = coalesce(s.subsection, '')
        -- Коэффициент берётся по единице конкретного наблюдения, а не по единице ряда:
        -- выпуски одного издания объявляют единицу для России в целом по-разному.
        LEFT JOIN map_unit_scale AS us ON us.source_unit = s.indicator_unit
        LEFT JOIN map_unit_scale AS su ON su.source_unit = m.source_unit
        -- Уровень и относительная величина под одним ключом («млн рублей» и «% к предыдущему
        -- году»): в ряд идут только строки его единицы, иначе max() смешал бы их.
        WHERE NOT (
            coalesce(us.unit_kind, 'unknown') <> 'unknown'
            AND coalesce(su.unit_kind, 'unknown') <> 'unknown'
            AND (us.unit_kind IN ({RELATIVE_KINDS})) <> (su.unit_kind IN ({RELATIVE_KINDS}))
        )
        GROUP BY 1, 2, 3, 4, 5
        -- Упорядочивание задаёт физическое размещение строк в группах: страница
        -- пересмотров запрашивает все версии одного наблюдения, и они должны лежать рядом.
        ORDER BY 1, 2, 3, 5
        """
    )

    count = require_row(
        connection.execute("SELECT count(*) FROM fact_vintage").fetchone(), "count"
    )[0]
    logger.info("Загружено версий наблюдений: %d", count)
    return count


# Копия года в выпуске набора: не меньше COPY_MIN_REGIONS общих субъектов, совпадает
# не меньше COPY_MIN_SHARE значений, второй год позже первого на COPY_MIN_GAP лет и более.
COPY_MIN_REGIONS = 10
COPY_MIN_SHARE = 0.9
COPY_MIN_GAP = 5


def drop_year_copies(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """
    Убрать из выпусков набора годы, значения которых повторяют другой год того же ряда.

    В сборниках набора столбец года бывает подписан не тем годом: у рядов о малом
    и среднем предпринимательстве значения «2010» и «2011» по всем субъектам совпадают
    со значениями 2020–2023. Копия — пара лет ряда, далёких друг от друга, с почти
    одинаковыми значениями по субъектам (в одном выпуске или в разных), если ни один
    из двух годов не совпадает так же с соседним годом (медленно меняющийся ряд —
    не копия). Год вне сплошного блока лет второго года удаляется из всех выпусков ряда;
    если оба в одном блоке, неизвестно, какой верен, и оба удаляются из своих выпусков. Запускается
    до выпусков внешних источников: проверяются только сборники набора.
    """
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE year_copy AS
        WITH v AS (
            SELECT v.series_key, v.edition_code, v.territory_code, v.year, v.value
            FROM fact_vintage AS v
            JOIN dim_territory AS t ON t.territory_code = v.territory_code
            WHERE t.level = 'region' AND NOT t.is_aggregate
              AND v.value IS NOT NULL AND v.value <> 0
        ),
        -- Годы ряда по всем выпускам: соседние годы и сплошные блоки лет.
        years AS (
            SELECT series_key, year,
                   lag(year) OVER w AS previous_year,
                   lead(year) OVER w AS next_year,
                   year - row_number() OVER w AS block
            FROM (SELECT DISTINCT series_key, year FROM v)
            WINDOW w AS (PARTITION BY series_key ORDER BY year)
        ),
        -- Пары «год выпуска — год выпуска» одного ряда с почти одинаковыми значениями:
        -- копия бывает и между изданиями («2011» сборника 2022 года = «2021» сборника 2024-го).
        alike AS (
            SELECT x.series_key,
                   x.edition_code AS first_edition, x.year AS first_year,
                   y.edition_code AS second_edition, y.year AS second_year
            FROM v AS x
            JOIN v AS y
              ON y.series_key = x.series_key
             AND y.territory_code = x.territory_code AND y.year > x.year
            GROUP BY ALL
            HAVING count(*) >= {COPY_MIN_REGIONS}
               AND count(*) FILTER (WHERE x.value = y.value) >= {COPY_MIN_SHARE} * count(*)
        ),
        steady AS (
            SELECT DISTINCT y.series_key, y.year
            FROM years AS y
            JOIN alike AS a
              ON a.series_key = y.series_key
             AND ((a.first_year = y.previous_year AND a.second_year = y.year)
               OR (a.first_year = y.year AND a.second_year = y.next_year))
        ),
        copies AS (
            SELECT a.*, f.block <> s.block AS detached
            FROM alike AS a
            JOIN years AS f ON f.series_key = a.series_key AND f.year = a.first_year
            JOIN years AS s ON s.series_key = a.series_key AND s.year = a.second_year
            WHERE a.second_year - a.first_year >= {COPY_MIN_GAP}
              AND NOT EXISTS (
                  SELECT 1 FROM steady AS q
                  WHERE q.series_key = a.series_key
                    AND q.year IN (a.first_year, a.second_year)
              )
        )
        -- Оторванный год — подпись столбца, которой в ряду нет веры: он убирается из всех
        -- выпусков (в соседнем издании под ним бывают одни нули, их сравнение не ловит).
        SELECT DISTINCT series_key, NULL AS edition_code, first_year AS year
        FROM copies WHERE detached
        UNION
        SELECT DISTINCT series_key, first_edition, first_year FROM copies WHERE NOT detached
        UNION
        SELECT DISTINCT series_key, second_edition, second_year FROM copies WHERE NOT detached
        """
    )
    pairs = connection.execute(
        "SELECT series_key, coalesce(edition_code, '*'), list(DISTINCT year ORDER BY year) "
        "FROM year_copy GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    removed = connection.execute(
        """
        DELETE FROM fact_vintage AS v
        USING year_copy AS c
        WHERE v.series_key = c.series_key AND v.year = c.year
          AND (c.edition_code IS NULL OR v.edition_code = c.edition_code)
        RETURNING 1
        """
    ).fetchall()
    connection.execute("DROP TABLE year_copy")
    for series_key, edition_code, years in pairs:
        logger.info("Копия года в выпуске %s, ряд %s: %s", edition_code, series_key, years)
    return {
        "series": len({series_key for series_key, _, _ in pairs}),
        "years": sum(len(years) for _, _, years in pairs),
        "rows": len(removed),
    }


# Сверка значения по России с суммой субъектов: допуск и доля субъектов в сумме.
COUNTRY_SUM_TOLERANCE = 0.05
COUNTRY_SUM_MIN_REGIONS = 60
COUNTRY_SCALE_FACTORS = (1000.0, 1_000_000.0, 0.001)


def reconcile_country_scale(connection: duckdb.DuckDBPyConnection) -> int:
    """
    Привести значение по России к единице субъектов, когда объявление для России ненадёжно.

    Строки вида «; для значений в целом по России: млрд руб» не дают множителя (единица
    субъектов — в разрезе), а выпуски держат объявление по-разному: «Торговля в России
    2023» даёт Россию в млрд руб., «Торговля в России 2025» при том же объявлении — в млн.
    Множитель (1000, 1 000 000 или 1/1000) применяется к значению выпуска, только если
    с ним значение сходится с суммой субъектов того же выпуска, а без него — нет.
    Проверяются ряды уровня с таким объявлением хотя бы в одной строке набора.
    """
    factors = ", ".join(f"({factor})" for factor in COUNTRY_SCALE_FACTORS)
    changed = connection.execute(
        f"""
        WITH flagged AS (
            SELECT DISTINCT m.series_key
            FROM src AS s
            JOIN map_series AS m
              ON m.indicator_code = s.indicator_code
             AND m.subsection_raw = coalesce(s.subsection, '')
            JOIN map_unit_scale AS u ON u.source_unit = s.indicator_unit
            JOIN dim_series AS d ON d.series_key = m.series_key
            JOIN dim_unit AS du ON du.unit_code = d.unit_code
            WHERE u.country_scale_unknown AND du.kind IN ({LEVEL_KINDS})
        ),
        regions AS (
            SELECT v.series_key, v.edition_code, v.year, sum(v.value) AS total, count(*) AS n
            FROM fact_vintage AS v
            JOIN dim_territory AS t ON t.territory_code = v.territory_code
            WHERE t.level = 'region' AND NOT t.is_aggregate AND v.value IS NOT NULL
              AND v.series_key IN (SELECT series_key FROM flagged)
            GROUP BY ALL
        ),
        scaled AS (
            SELECT v.series_key, v.edition_code, v.year, v.territory_code, f.factor
            FROM fact_vintage AS v
            JOIN dim_territory AS t ON t.territory_code = v.territory_code
            JOIN regions AS r
              ON r.series_key = v.series_key AND r.edition_code = v.edition_code
             AND r.year = v.year
            CROSS JOIN (VALUES {factors}) AS f(factor)
            WHERE t.level = 'country' AND v.value > 0 AND r.total > 0
              AND r.n >= {COUNTRY_SUM_MIN_REGIONS}
              AND abs(v.value - r.total) > {COUNTRY_SUM_TOLERANCE} * r.total
              AND abs(v.value * f.factor - r.total) <= {COUNTRY_SUM_TOLERANCE} * r.total
        )
        UPDATE fact_vintage AS v
        SET value = v.value * c.factor
        FROM scaled AS c
        WHERE v.series_key = c.series_key AND v.edition_code = c.edition_code
          AND v.year = c.year AND v.territory_code = c.territory_code
        RETURNING v.series_key, v.edition_code, v.year
        """
    ).fetchall()
    for series_key, edition_code, year in changed:
        logger.info(
            "Россия приведена к единице субъектов: %s, %s, %s", series_key, edition_code, year
        )
    return len(changed)


def apply_corrections(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """
    Исправить доказанные ошибки исходного набора в версиях наблюдений.

    Меняется только версия с записанным ошибочным значением: другие версии того же года целы,
    а поправка, значение которой поставщик исправил сам, устаревает и попадает в отчёт сборки.
    """
    path = settings.REFERENCE_DIR / CORRECTIONS_FILE
    items = json.loads(path.read_bytes().decode("utf-8"))["corrections"]
    stale = []
    for item in items:
        changed = connection.execute(
            """
            UPDATE fact_vintage
            SET value = ?, quality = ?, flags = flags | ?
            WHERE series_key = ? AND territory_code = ? AND year = ?
              AND value IS NOT DISTINCT FROM ?
            RETURNING 1
            """,
            [
                item["value"],
                int(ValueQuality.OBSERVED),
                int(ValueFlag.CORRECTED),
                item["series"],
                item["territory"],
                item["year"],
                item["was"],
            ],
        ).fetchall()
        if not changed:
            stale.append(f"{item['series']} {item['territory']} {item['year']}")
    if stale:
        logger.warning("Поправки без ошибочного значения в наборе: %s", ", ".join(stale))
    return {"applied": len(items) - len(stale), "stale": len(stale)}


def rank_editions(connection: duckdb.DuckDBPyConnection) -> int:
    """
    Упорядочить выпуски по времени выхода: больший номер — более поздний.

    У сборников набора дата неизвестна — конец года издания; из сборников одного года
    первым идёт издание с меньшим кодом, как было до появления внешних источников.
    """
    connection.execute(
        """
        UPDATE dim_edition AS e
        SET edition_rank = r.edition_rank
        FROM (
            SELECT
                edition_code,
                CAST(row_number() OVER (
                    ORDER BY edition_year,
                             coalesce(released_on, make_date(edition_year, 12, 31)),
                             edition_code DESC
                ) AS SMALLINT) AS edition_rank
            FROM dim_edition
        ) AS r
        WHERE r.edition_code = e.edition_code
        """
    )
    return require_row(connection.execute("SELECT count(*) FROM dim_edition").fetchone(), "count")[
        0
    ]


def build_observations(connection: duckdb.DuckDBPyConnection) -> int:
    """Построить каноническую таблицу наблюдений с числом различающихся версий."""
    connection.execute(
        """
        INSERT INTO fact_observation
        WITH ranked AS (
            SELECT
                v.series_key,
                v.territory_code,
                v.year,
                v.edition_code,
                v.edition_year,
                v.value,
                v.quality,
                v.flags,
                row_number() OVER (
                    PARTITION BY v.series_key, v.territory_code, v.year
                    -- Приоритет: сначала непропущенные значения, затем более поздние выпуски.
                    ORDER BY (v.value IS NULL) ASC, e.edition_rank DESC
                ) AS priority,
                -- Число различных значений среди версий: показывает факт пересмотра.
                count(DISTINCT v.value) OVER (
                    PARTITION BY v.series_key, v.territory_code, v.year
                ) AS distinct_values
            FROM fact_vintage AS v
            JOIN dim_edition  AS e ON e.edition_code = v.edition_code
        )
        SELECT
            r.series_key,
            ds.indicator_code,
            ds.section_code,
            r.territory_code,
            dt.level                                        AS territory_level,
            dt.district_code,
            dt.is_aggregate,
            r.year,
            r.value,
            r.quality,
            r.edition_code,
            r.edition_year,
            CAST(greatest(r.distinct_values - 1, 0) AS TINYINT) AS revision_count,
            r.flags
        FROM ranked        AS r
        JOIN dim_series    AS ds ON ds.series_key = r.series_key
        JOIN dim_territory AS dt ON dt.territory_code = r.territory_code
        WHERE r.priority = 1
        -- Ключ ряда — ведущим столбцом: почти каждый запрос отбирает строки одного ряда,
        -- и от порядка размещения зависит, сколько групп строк придётся прочитать.
        ORDER BY r.series_key, r.territory_code, r.year
        """
    )

    count = require_row(
        connection.execute("SELECT count(*) FROM fact_observation").fetchone(), "count"
    )[0]
    logger.info("Канонических наблюдений: %d", count)
    return count


def build_revisions(connection: duckdb.DuckDBPyConnection) -> int:
    """Построить витрину пересмотров: только различающиеся непропущенные значения."""
    connection.execute(
        """
        INSERT INTO mart_revision
        WITH versions AS (
            SELECT v.series_key, v.territory_code, v.year, e.edition_rank, v.value
            FROM fact_vintage AS v
            JOIN dim_edition  AS e ON e.edition_code = v.edition_code
            WHERE v.value IS NOT NULL
        ),
        aggregated AS (
            SELECT
                series_key,
                territory_code,
                year,
                count(*)                  AS edition_count,
                count(DISTINCT value)     AS distinct_values,
                -- arg_min и arg_max возвращают значение, соответствующее крайним выпускам.
                arg_min(value, edition_rank) AS first_value,
                arg_max(value, edition_rank) AS last_value
            FROM versions
            GROUP BY 1, 2, 3
        )
        SELECT
            series_key,
            territory_code,
            year,
            CAST(edition_count AS TINYINT) AS edition_count,
            first_value,
            last_value,
            last_value - first_value       AS abs_change,
            CASE
                WHEN first_value IS NULL OR first_value = 0 THEN NULL
                ELSE (last_value - first_value) / abs(first_value)
            END                            AS rel_change
        FROM aggregated
        WHERE distinct_values > 1
        ORDER BY series_key, territory_code, year
        """
    )

    count = require_row(
        connection.execute("SELECT count(*) FROM mart_revision").fetchone(), "count"
    )[0]
    logger.info("Пересмотренных наблюдений: %d", count)
    return count


def observation_quality_summary(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Собрать сводку по качеству загруженных наблюдений для журнала запуска."""
    row = require_row(
        connection.execute(
            f"""
        SELECT
            count(*)                                                      AS total,
            count(*) FILTER (WHERE quality = {ValueQuality.OBSERVED})     AS observed,
            count(*) FILTER (WHERE quality = {ValueQuality.NO_DATA})      AS no_data,
            count(*) FILTER (WHERE quality = {ValueQuality.HIDDEN})       AS hidden,
            count(*) FILTER (WHERE value < 0)                             AS negative
        FROM fact_observation
        """
        ).fetchone(),
        "observation_quality_summary",
    )

    return {
        "total": row[0],
        "observed": row[1],
        "no_data": row[2],
        "hidden": row[3],
        "negative_values": row[4],
    }
