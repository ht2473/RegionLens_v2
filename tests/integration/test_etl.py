"""
Проверки конвейера сборки на собранном складе — по результату каждого правила:
ошибка здесь даёт правдоподобные, но неверные числа.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import duckdb
import pytest

from apps.catalog.constants import ValueQuality
from apps.warehouse import duckdb_client
from tests.support import synthetic

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture
def connection(warehouse_file: Path) -> Any:
    """Соединение с собранным тестовым складом только на чтение."""
    handle = duckdb.connect(str(warehouse_file), read_only=True)
    try:
        yield handle
    finally:
        handle.close()


class TestDimensions:
    """Измерения склада."""

    def test_territories_are_loaded_from_reference(self, connection: Any) -> None:
        """
        Территории берутся из справочника проекта, а не из исходных данных.

        В источнике код ОКТМО у страны и всех округов одинаков, поэтому построить
        по нему измерение невозможно.
        """
        levels = dict(
            connection.execute("SELECT level, count(*) FROM dim_territory GROUP BY 1").fetchall()
        )
        assert levels == {"country": 1, "federal_district": 8, "region": 87}

    def test_aggregate_territories_are_marked(self, connection: Any) -> None:
        """
        Составные территории помечены признаком.

        «Тюменская область (с автономными округами)» — сумма трёх субъектов.
        Без признака она попадала бы в рейтинги и картограммы, удваивая данные.
        """
        codes = {
            row[0]
            for row in connection.execute(
                "SELECT territory_code FROM dim_territory WHERE is_aggregate"
            ).fetchall()
        }
        assert codes == {"RU-ARK-AGG", "RU-TYU-AGG"}

    def test_series_are_pairs_of_indicator_and_subsection(self, connection: Any) -> None:
        """Единица анализа — пара «показатель + разрез», а не показатель."""
        series_count = connection.execute("SELECT count(*) FROM dim_series").fetchone()[0]
        indicator_count = connection.execute("SELECT count(*) FROM dim_indicator").fetchone()[0]

        assert indicator_count == len(synthetic.SERIES_SPECS)
        assert series_count == sum(len(spec.subsections) for spec in synthetic.SERIES_SPECS)
        assert series_count > indicator_count

    def test_unit_with_country_scale_is_recognised(self, connection: Any) -> None:
        """Укрупнённая единица для страны распознана: итог в миллиардах при регионах в миллионах."""
        scale = connection.execute(
            """
            SELECT u.country_scale
            FROM dim_unit u
            JOIN dim_series s ON s.unit_code = u.unit_code
            WHERE s.indicator_code = ?
            """,
            [synthetic.GRP_TOTAL_CODE],
        ).fetchone()
        assert scale is not None
        assert scale[0] == pytest.approx(1000.0)


class TestFacts:
    """Наблюдения и правила их приведения."""

    def test_sentinels_become_missing_values(self, connection: Any) -> None:
        """
        Заглушки источника переведены в отсутствие значения.

        Числа −99999999 и −77777777 обозначают «нет данных» и «значение скрыто».
        Загруженные как есть, они исказили бы любое среднее.
        """
        leaked = connection.execute(
            "SELECT count(*) FROM fact_observation WHERE value < -1000000"
        ).fetchone()[0]
        assert leaked == 0

        no_data = connection.execute(
            "SELECT count(*) FROM fact_observation WHERE quality = ?",
            [int(ValueQuality.NO_DATA)],
        ).fetchone()[0]
        hidden = connection.execute(
            "SELECT count(*) FROM fact_observation WHERE quality = ?",
            [int(ValueQuality.HIDDEN)],
        ).fetchone()[0]
        assert no_data > 0
        assert hidden > 0

    def test_missing_reason_is_preserved(self, connection: Any) -> None:
        """У пропуска сохранена причина: «нет данных» и «скрыто» — разные случаи."""
        rows = connection.execute(
            """
            SELECT DISTINCT quality
            FROM fact_observation
            WHERE value IS NULL
            """
        ).fetchall()
        qualities = {row[0] for row in rows}
        assert qualities <= {int(ValueQuality.NO_DATA), int(ValueQuality.HIDDEN)}
        assert len(qualities) == 2

    def test_negative_values_survive(self, connection: Any) -> None:
        """
        Законные отрицательные значения не обнулены.

        Коэффициент естественного прироста отрицателен в большинстве субъектов;
        обнуление таких значений исказило бы демографическую часть системы.
        """
        negatives = connection.execute(
            """
            SELECT count(*)
            FROM fact_observation
            WHERE indicator_code = ? AND value < 0
            """,
            [synthetic.NATURAL_GROWTH_CODE],
        ).fetchone()[0]
        assert negatives > 0

    def test_later_edition_wins(self, connection: Any) -> None:
        """
        Конфликт выпусков разрешается в пользу более позднего.

        Ранний выпуск публикует значение на три процента ниже. Каноническим должно
        стать значение позднего выпуска, а обе версии — сохраниться.
        """
        row = connection.execute(
            """
            SELECT o.value, o.edition_year, count(v.value)
            FROM fact_observation o
            JOIN fact_vintage v
              ON v.series_key = o.series_key
             AND v.territory_code = o.territory_code
             AND v.year = o.year
            WHERE o.indicator_code = ?
              AND o.year = ?
              AND o.territory_code = ?
            GROUP BY 1, 2
            """,
            [synthetic.WAGE_CODE, synthetic.REVISION_LAST_YEAR, "RU-BEL"],
        ).fetchone()

        assert row is not None
        value, edition_year, vintage_count = row
        assert vintage_count == 2
        assert edition_year == 2025

        earlier = connection.execute(
            """
            SELECT value FROM fact_vintage
            WHERE series_key IN (SELECT series_key FROM dim_series WHERE indicator_code = ?)
              AND territory_code = ? AND year = ? AND edition_year = 2022
            """,
            [synthetic.WAGE_CODE, "RU-BEL", synthetic.REVISION_LAST_YEAR],
        ).fetchone()[0]
        assert earlier < value

    def test_revisions_are_recorded(self, connection: Any) -> None:
        """Расхождения между выпусками попали в витрину пересмотров."""
        row = connection.execute(
            """
            SELECT count(*), avg(rel_change)
            FROM mart_revision
            """
        ).fetchone()
        assert row[0] > 0
        # Позднее значение выше раннего на 3/97 доли.
        assert row[1] == pytest.approx(0.03 / 0.97, rel=0.01)

    def test_country_value_is_scaled_to_region_unit(self, connection: Any) -> None:
        """
        Значение по стране приведено к единице измерения субъектов.

        Проверяется сходимость: итог по России должен совпадать с суммой субъектов,
        а не отличаться от неё на три порядка.
        """
        row = connection.execute(
            """
            WITH regions AS (
                SELECT year, sum(value) AS total
                FROM fact_observation
                WHERE indicator_code = ?
                  AND territory_level = 'region'
                  AND NOT is_aggregate
                GROUP BY 1
            )
            SELECT r.total, o.value
            FROM regions r
            JOIN fact_observation o
              ON o.year = r.year AND o.territory_code = 'RU' AND o.indicator_code = ?
            WHERE r.year = 2015
            """,
            [synthetic.GRP_TOTAL_CODE, synthetic.GRP_TOTAL_CODE],
        ).fetchone()
        assert row is not None
        assert row[1] == pytest.approx(row[0], rel=0.001)


class TestMarts:
    """Витрины покрытия, статистик и рангов."""

    def test_coverage_separates_analysis_ready_series(self, connection: Any) -> None:
        """
        Ряд с недостаточным покрытием не помечен пригодным к анализу.

        Ряд по двадцати субъектам за пять лет нельзя ставить в рейтинг и нельзя
        кластеризовать: результат выглядел бы правдоподобно и был бы бессмысленным.
        """
        rows = dict(
            connection.execute(
                """
                SELECT s.indicator_code, bool_and(c.is_analysis_ready)
                FROM mart_series_coverage c
                JOIN dim_series s ON s.series_key = c.series_key
                GROUP BY 1
                """
            ).fetchall()
        )
        assert rows[synthetic.PARTIAL_COVERAGE_CODE] is False
        assert rows[synthetic.POPULATION_CODE] is True

    def test_ranks_exclude_aggregate_territories(self, connection: Any) -> None:
        """Составные территории не участвуют в рейтингах."""
        count = connection.execute(
            """
            SELECT count(*)
            FROM mart_rank
            WHERE territory_code IN ('RU-ARK-AGG', 'RU-TYU-AGG', 'RU')
            """
        ).fetchone()[0]
        assert count == 0

    def test_rank_positions_are_consecutive(self, connection: Any) -> None:
        """Ранги внутри года образуют непрерывную последовательность с единицы."""
        row = connection.execute(
            """
            SELECT min(rank_desc), max(rank_desc), count(*)
            FROM mart_rank
            WHERE series_key = (
                SELECT series_key FROM dim_series WHERE indicator_code = ? LIMIT 1
            ) AND year = 2015
            """,
            [synthetic.POPULATION_CODE],
        ).fetchone()
        assert row[0] == 1
        assert row[1] == row[2]

    def test_statistics_match_observations(self, connection: Any) -> None:
        """Статистики распределения рассчитаны по тем же наблюдениям."""
        row = connection.execute(
            """
            WITH direct AS (
                SELECT avg(value) AS mean_value, count(*) AS observations
                FROM fact_observation
                WHERE series_key = (
                    SELECT series_key FROM dim_series WHERE indicator_code = ? LIMIT 1
                )
                  AND year = 2018
                  AND territory_level = 'region'
                  AND NOT is_aggregate
                  AND value IS NOT NULL
            )
            SELECT d.mean_value, d.observations, s.mean_value, s.observations
            FROM direct d
            JOIN mart_series_stats s
              ON s.year = 2018
             AND s.series_key = (
                    SELECT series_key FROM dim_series WHERE indicator_code = ? LIMIT 1
                )
            """,
            [synthetic.UNEMPLOYMENT_CODE, synthetic.UNEMPLOYMENT_CODE],
        ).fetchone()
        assert row[0] == pytest.approx(row[2])
        assert row[1] == row[3]


class TestUniqueKeys:
    """Единственность логических ключей склада: ограничений PRIMARY KEY в схеме нет."""

    def test_built_warehouse_has_unique_keys(self, connection: Any) -> None:
        """Собранный склад проходит проверку ключей."""
        from apps.warehouse.etl.pipeline import UNIQUE_KEYS, check_unique_keys

        assert check_unique_keys(connection) == len(UNIQUE_KEYS)

    def test_every_table_of_the_schema_has_a_key(self) -> None:
        """Ключ описан у каждой таблицы схемы, кроме версий значений: их по ключу несколько."""
        from apps.warehouse.etl.pipeline import SCHEMA_PATH, UNIQUE_KEYS

        tables = set(re.findall(r"CREATE TABLE (\w+)", SCHEMA_PATH.read_text("utf-8")))
        assert tables - set(UNIQUE_KEYS) == {"fact_vintage"}
        assert set(UNIQUE_KEYS) <= tables

    def test_repeated_key_stops_the_build(self) -> None:
        """Повтор ключа прерывает сборку до синхронизации справочников."""
        from apps.warehouse.etl.pipeline import SCHEMA_PATH, EtlError, check_unique_keys

        handle = duckdb.connect()
        try:
            handle.execute(SCHEMA_PATH.read_text("utf-8"))
            handle.execute("INSERT INTO meta_build VALUES ('version', '1'), ('version', '2')")
            with pytest.raises(EtlError, match="meta_build"):
                check_unique_keys(handle)
        finally:
            handle.close()


class TestCatalogSynchronisation:
    """Перенос измерений склада в справочники PostgreSQL."""

    def test_series_are_visible_in_catalog(self, warehouse: Any) -> None:
        """Каталог показателей заполнен и связан со складом."""
        from apps.catalog.models import Indicator, Section, Series

        assert Section.objects.count() == synthetic.SECTION_COUNT
        assert Indicator.objects.count() == len(synthetic.SERIES_SPECS)
        assert Series.objects.count() == warehouse.series_count
        assert Series.objects.filter(is_analysis_ready=True).exists()

    def test_methodology_note_produces_break(self, warehouse: Any) -> None:
        """
        Примечание о смене методики превращено в разрыв сопоставимости.

        Именно эта отметка объясняет пользователю, почему ряд нельзя сравнивать
        через границу 2017 года.
        """
        from apps.catalog.models import SeriesBreak

        breaks = SeriesBreak.objects.filter(series__indicator__code=warehouse.break_code)
        assert breaks.exists()
        assert breaks.filter(year=2017).exists()

    def test_superseded_series_take_breaks_from_source_notes(self, warehouse: Any) -> None:
        """
        Ряд, значения которого таблица источника заменяет целиком, размечается её сносками;
        примечание набора о прежних значениях отметки ему не даёт, другому ряду — даёт.
        """
        from apps.catalog.constants import BreakKind
        from apps.catalog.models import SeriesBreak
        from apps.warehouse.etl.catalog_sync import sync_methodology

        grp = f"{synthetic.GRP_PER_CAPITA_CODE}:00"
        income = f"{synthetic.INCOME_CODE}:00"
        census = "Данные за 2011-2021 годы публикуются без учета итогов ВПН-2020"
        footnotes = [
            "Данные динамического ряда, начиная с 2016 года, содержат изменения, связанные "
            "с внедрением международной методологии оценки жилищных услуг, производимых "
            "и потребляемых собственниками жилья; оценкой потребления основного капитала, "
            "исходя из его текущей рыночной стоимости.",
            "Данные динамического ряда, начиная с 2017 года, содержат изменения, связанные "
            "с включением в формирование показателей деятельности: кредитных организаций; "
            "финансовых услуг, кроме услуг по страхованию и пенсионному обеспечению; "
            "негосударственных пенсионных фондов; вспомогательной в сфере финансовых услуг "
            "и страхования.",
            "Без учета статистической информации по Донецкой Народной Республике (ДНР), "
            "Луганской Народной Республике (ЛНР), Запорожской и Херсонской областям.",
        ]
        memory = duckdb.connect()
        memory.execute(
            "CREATE TABLE src (indicator_code VARCHAR, subsection VARCHAR, comment VARCHAR)"
        )
        memory.executemany(
            "INSERT INTO src VALUES (?, NULL, ?)",
            [[synthetic.GRP_PER_CAPITA_CODE, census], [synthetic.INCOME_CODE, census]],
        )
        memory.execute(
            "CREATE TABLE map_series (series_key VARCHAR, indicator_code VARCHAR, "
            "subsection_raw VARCHAR)"
        )
        memory.executemany(
            "INSERT INTO map_series VALUES (?, ?, '')",
            [[grp, synthetic.GRP_PER_CAPITA_CODE], [income, synthetic.INCOME_CODE]],
        )
        memory.execute(
            "CREATE TABLE mart_source_link (series_key VARCHAR, source_code VARCHAR, "
            "mode VARCHAR, link VARCHAR, junction_year SMALLINT, within_share DOUBLE, "
            "compared_from SMALLINT, compared_to SMALLINT)"
        )
        memory.execute(
            "INSERT INTO mart_source_link VALUES "
            "(?, 'rosstat_grp', 'supersede', 'revision', NULL, 1.0, NULL, NULL)",
            [grp],
        )
        memory.execute(
            "CREATE TABLE mart_source_note (series_key VARCHAR, source_code VARCHAR, "
            "edition_code VARCHAR, position SMALLINT, note_text VARCHAR)"
        )
        memory.executemany(
            "INSERT INTO mart_source_note VALUES (?, 'rosstat_grp', 'rel', ?, ?)",
            [[grp, position, text] for position, text in enumerate(footnotes)],
        )
        memory.execute(
            "CREATE TABLE dim_edition (source_code VARCHAR, publication_ru VARCHAR, "
            "publication_en VARCHAR)"
        )
        try:
            sync_methodology(memory)
        finally:
            memory.close()

        marks = list(SeriesBreak.objects.filter(series__key=grp).order_by("year"))
        assert [(item.year, item.kind, item.note_id) for item in marks] == [
            (2016, BreakKind.METHODOLOGY, None),
            (2017, BreakKind.METHODOLOGY, None),
        ]
        assert marks[0].description_ru == footnotes[0]
        assert marks[0].description_en.startswith("From 2016")
        assert [
            (item.year, item.kind) for item in SeriesBreak.objects.filter(series__key=income)
        ] == [(2022, BreakKind.COVERAGE)]

    def test_subject_change_is_marked_for_that_subject(self, warehouse: Any) -> None:
        """Отметка перемены в составе одного субъекта хранит его территорию."""
        from apps.catalog.constants import BreakKind
        from apps.catalog.models import SeriesBreak
        from apps.warehouse.etl.catalog_sync import sync_methodology

        key = f"{synthetic.INCOME_CODE}:00"
        memory = duckdb.connect()
        memory.execute(
            "CREATE TABLE src (indicator_code VARCHAR, subsection VARCHAR, comment VARCHAR)"
        )
        memory.execute(
            "INSERT INTO src VALUES (?, NULL, ?)",
            [
                synthetic.INCOME_CODE,
                "До 2006 г. — Пермская область без учета Коми-Пермяцкого автономного округа",
            ],
        )
        memory.execute(
            "CREATE TABLE map_series (series_key VARCHAR, indicator_code VARCHAR, "
            "subsection_raw VARCHAR)"
        )
        memory.execute("INSERT INTO map_series VALUES (?, ?, '')", [key, synthetic.INCOME_CODE])
        try:
            sync_methodology(memory)
        finally:
            memory.close()

        marks = SeriesBreak.objects.filter(series__key=key).select_related("territory")
        assert [(item.year, item.kind, item.territory.code) for item in marks] == [
            (2006, BreakKind.TERRITORY, "RU-PER")
        ]

    def test_addresses_survive_resync(self, warehouse: Any, warehouse_file: Any) -> None:
        """
        Повторная синхронизация не меняет адресов разделов, показателей и изданий.

        Если считать собственный адрес записи занятым, он чередуется с «-2» от сборки
        к сборке, и ссылки на страницы показателей ломаются через раз.
        """
        from apps.catalog.models import Indicator, Publication, Section
        from tests.support.warehouse import sync_catalog

        def addresses() -> tuple[dict[str, str], ...]:
            return (
                dict(Section.objects.values_list("source_name", "slug")),
                dict(Indicator.objects.values_list("code", "slug")),
                dict(Publication.objects.values_list("name_ru", "slug")),
            )

        before = addresses()
        sync_catalog(warehouse_file)
        assert addresses() == before
        assert not any(slug.endswith("-2") for slug in before[1].values())

    def test_swapped_addresses_are_taken_back(self, warehouse: Any, warehouse_file: Any) -> None:
        """Адрес, доставшийся другому показателю, возвращается без нарушения уникальности."""
        from apps.catalog.models import Indicator
        from tests.support.warehouse import sync_catalog

        first, second = Indicator.objects.order_by("code")[:2]
        expected = {first.code: first.slug, second.code: second.slug}
        Indicator.objects.filter(pk=first.pk).update(slug="swap")
        Indicator.objects.filter(pk=second.pk).update(slug=expected[first.code])
        Indicator.objects.filter(pk=first.pk).update(slug=expected[second.code])
        sync_catalog(warehouse_file)
        found = Indicator.objects.filter(code__in=expected).values_list("code", "slug")
        assert dict(found) == expected

    def test_polarity_is_detected(self, warehouse: Any) -> None:
        """
        Направление «лучше — хуже» выведено из названия показателя.

        Без полярности рейтинг по уровню безработицы возглавили бы худшие субъекты.
        """
        from apps.catalog.constants import Polarity
        from apps.catalog.models import Indicator

        unemployment = Indicator.objects.get(code=synthetic.UNEMPLOYMENT_CODE)
        assert unemployment.polarity == Polarity.NEGATIVE

    def test_units_are_keyed_by_warehouse_code(self, warehouse: Any, connection: Any) -> None:
        """
        Единица каждого ряда — та же, что в складе, по коду, а не по названию источника.

        У 43 единиц, восстановленных из формулировок, в источнике одно название — ND.
        """
        from apps.catalog.models import Series, Unit

        codes = dict(connection.execute("SELECT series_key, unit_code FROM dim_series").fetchall())
        assert Unit.objects.filter(code="").count() == 0
        for series in Series.objects.select_related("unit"):
            assert series.unit is not None
            assert series.unit.code == codes[series.key]

    def test_featured_set_is_applied(self, warehouse: Any) -> None:
        """
        Основной набор задаёт направленность своих рядов и отмечает ключевые показатели.

        Без этого конструктор индекса и API брали угаданную направленность и расходились
        с паспортом региона, а отбор ``is_featured`` в API не находил ничего.
        """
        from apps.catalog.constants import Polarity
        from apps.catalog.models import Indicator, Series
        from apps.warehouse.queries.common import featured_set

        featured = featured_set().by_key()
        present = Series.objects.filter(key__in=featured)
        assert present.exists()
        for series in present:
            assert series.polarity == featured[series.key].polarity
        population = Series.objects.get(indicator__code=synthetic.POPULATION_CODE)
        assert population.polarity == Polarity.NEUTRAL
        assert set(Indicator.objects.filter(is_featured=True).values_list("pk", flat=True)) == set(
            present.values_list("indicator_id", flat=True)
        )


def test_connection_spills_to_directory_of_its_process(warehouse: Any, settings: Any) -> None:
    """Выгрузка на диск — в каталог своего процесса: общий каталог роняет DuckDB."""
    directory = Path(duckdb_client.fetch_scalar("SELECT current_setting('temp_directory')"))

    assert directory.name == str(os.getpid())
    assert directory.parent == settings.DUCKDB_PATH.parent / f"{settings.DUCKDB_PATH.name}.tmp"
