"""
Перенос измерений склада в справочники PostgreSQL — только метаданные.

Записи обновляются по устойчивым ключам, ссылки на них сохраняются.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import duckdb
from django.db import transaction

from apps.catalog.constants import BreakKind, Polarity
from apps.catalog.models import (
    Indicator,
    MethodologyNote,
    Publication,
    Section,
    Series,
    SeriesBreak,
    SourceEdition,
    Territory,
    Unit,
)
from apps.core.utils.text import make_slug

from ..queries.common import featured_set
from .catalog_names import note_text_en
from .classify import NoteClassification, classify_note

logger = logging.getLogger(__name__)

# Размер пакета для массовых операций.
BATCH_SIZE = 500


def sync_all(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Выполнить полную синхронизацию справочников и вернуть статистику."""
    with transaction.atomic():
        sections = sync_sections(connection)
        publications, editions = sync_editions(connection)
        units = sync_units(connection)
        indicators = sync_indicators(connection)
        series = sync_series(connection)
        notes, links, breaks = sync_methodology(connection)

    statistics = {
        "sections": sections,
        "publications": publications,
        "editions": editions,
        "units": units,
        "indicators": indicators,
        "series": series,
        "notes": notes,
        "note_links": links,
        "breaks": breaks,
    }
    logger.info("Справочники синхронизированы: %s", statistics)
    return statistics


# ---------------------------------------------------------------------------------------
# Разделы, издания, единицы измерения
# ---------------------------------------------------------------------------------------


def sync_sections(connection: duckdb.DuckDBPyConnection) -> int:
    """Перенести разделы."""
    rows = connection.execute(
        "SELECT section_code, source_name, name_ru, indicator_count, series_count, name_en "
        "FROM dim_section ORDER BY name_ru, source_name"
    ).fetchall()

    # Адреса строятся заново в постоянном порядке: занятым считался бы и собственный
    # адрес записи, и он чередовался бы с «-2» от сборки к сборке.
    used_slugs: set[str] = set()
    slugs = {row[1]: _unique_slug(row[2], used_slugs, limit=140) for row in rows}
    _free_slugs(Section, "source_name", slugs)
    for order, row in enumerate(rows, 1):
        _section_code, source_name, name_ru, indicator_count, series_count, name_en = row
        defaults = {
            "slug": slugs[source_name],
            "name_ru": name_ru,
            "indicator_count": indicator_count,
            "series_count": series_count,
            "display_order": order,
        }
        if name_en:
            defaults["name_en"] = name_en
        Section.objects.update_or_create(source_name=source_name, defaults=defaults)
    return len(rows)


def sync_editions(connection: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Перенести издания и их выпуски."""
    rows = connection.execute(
        """
        SELECT edition_code, source_name, publication_ru, edition_year,
               observation_count, first_year, last_year
        FROM dim_edition
        ORDER BY publication_ru, edition_year
        """
    ).fetchall()

    publication_slugs: set[str] = set()
    publications: dict[str, Publication] = {}

    for row in rows:
        _edition_code, source_name, publication_ru, year = row[:4]
        observations, first_year, last_year = row[4:]

        publication = publications.get(publication_ru)
        if publication is None:
            publication, _ = Publication.objects.update_or_create(
                name_ru=publication_ru,
                defaults={"slug": _unique_slug(publication_ru, publication_slugs, limit=120)},
            )
            publications[publication_ru] = publication

        SourceEdition.objects.update_or_create(
            source_name=source_name,
            defaults={
                "publication": publication,
                "year": year,
                "observation_count": observations,
                "first_year": first_year,
                "last_year": last_year,
            },
        )

    return len(publications), len(rows)


def sync_units(connection: duckdb.DuckDBPyConnection) -> int:
    """
    Перенести единицы измерения вместе с их классификацией.

    Ключ — код единицы в складе: у 43 восстановленных единиц название источника одно — ``ND``.
    """
    rows = connection.execute(
        """
        SELECT unit_code, source_name, name_ru, name_en, short_name_ru, short_name_en,
               kind, multiplier, derived_from_name, country_scale, country_scale_unknown
        FROM dim_unit
        ORDER BY name_ru
        """
    ).fetchall()

    for (
        code,
        source_name,
        name_ru,
        name_en,
        short_name_ru,
        short_name_en,
        kind,
        multiplier,
        derived,
        country_scale,
        country_scale_unknown,
    ) in rows:
        Unit.objects.update_or_create(
            code=code,
            defaults={
                "source_name": source_name,
                "name_ru": name_ru,
                "name_en": name_en or "",
                "short_name_ru": short_name_ru or "",
                "short_name_en": short_name_en or "",
                "kind": kind,
                "multiplier": multiplier,
                "is_derived_from_name": derived,
                "country_scale": country_scale,
                "country_scale_unknown": country_scale_unknown,
            },
        )
    return len(rows)


# ---------------------------------------------------------------------------------------
# Показатели и ряды
# ---------------------------------------------------------------------------------------


def sync_indicators(connection: duckdb.DuckDBPyConnection) -> int:
    """Перенести показатели вместе со сводными характеристиками."""
    rows = connection.execute(
        """
        SELECT i.indicator_code,
               i.name_ru,
               s.source_name AS section_source,
               i.series_count,
               i.observation_count,
               i.first_year,
               i.last_year,
               -- Направленность показателя берётся у его первого ряда: в пределах
               -- показателя разрезы имеют одинаковую природу.
               any_value(ds.polarity) AS polarity,
               i.name_en
        FROM dim_indicator AS i
        JOIN dim_section   AS s  ON s.section_code = i.section_code
        LEFT JOIN dim_series AS ds ON ds.indicator_code = i.indicator_code
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 9
        ORDER BY i.name_ru, i.indicator_code
        """
    ).fetchall()

    sections = {section.source_name: section for section in Section.objects.all()}
    used_slugs: set[str] = set()
    slugs = {row[0]: _unique_slug(f"{row[1]}-{row[0][-6:]}", used_slugs, limit=140) for row in rows}
    _free_slugs(Indicator, "code", slugs)

    for (
        code,
        name_ru,
        section_source,
        series_count,
        observation_count,
        first_year,
        last_year,
        polarity,
        name_en,
    ) in rows:
        defaults = {
            "slug": slugs[code],
            "name_ru": name_ru,
            "section": sections[section_source],
            "series_count": series_count,
            "observation_count": observation_count,
            "first_year": first_year,
            "last_year": last_year,
            "polarity": polarity or Polarity.UNKNOWN,
        }
        if name_en:
            defaults["name_en"] = name_en
        Indicator.objects.update_or_create(code=code, defaults=defaults)
    return len(rows)


def sync_series(connection: duckdb.DuckDBPyConnection) -> int:
    """Перенести ряды вместе с характеристиками покрытия и числом пересмотров."""
    rows = connection.execute(
        """
        SELECT
            s.series_key,
            s.indicator_code,
            s.subsection_ru,
            s.has_subsection,
            s.polarity,
            s.unit_code,
            coalesce(c.observation_count, 0)     AS observation_count,
            coalesce(c.value_count, 0)           AS value_count,
            coalesce(c.no_data_count, 0)         AS no_data_count,
            coalesce(c.hidden_count, 0)          AS hidden_count,
            coalesce(c.region_count, 0)          AS region_count,
            coalesce(c.year_count, 0)            AS year_count,
            c.first_year,
            c.last_year,
            coalesce(c.region_coverage, 0)       AS region_coverage,
            coalesce(c.completeness, 0)          AS completeness,
            coalesce(c.longest_gap_free_span, 0) AS longest_span,
            coalesce(c.is_analysis_ready, FALSE) AS is_analysis_ready,
            coalesce(r.revision_count, 0)        AS revision_count
        FROM dim_series AS s
        LEFT JOIN mart_series_coverage AS c ON c.series_key = s.series_key
        LEFT JOIN (
            SELECT series_key, count(*) AS revision_count
            FROM mart_revision
            GROUP BY 1
        ) AS r ON r.series_key = s.series_key
        ORDER BY s.series_key
        """
    ).fetchall()

    indicators = {indicator.code: indicator for indicator in Indicator.objects.all()}
    units = {unit.code: unit for unit in Unit.objects.all()}

    for row in rows:
        (
            series_key,
            indicator_code,
            subsection_ru,
            has_subsection,
            polarity,
            unit_code,
            observation_count,
            value_count,
            no_data_count,
            hidden_count,
            region_count,
            year_count,
            first_year,
            last_year,
            region_coverage,
            completeness,
            longest_span,
            is_analysis_ready,
            revision_count,
        ) = row

        Series.objects.update_or_create(
            key=series_key,
            defaults={
                "indicator": indicators[indicator_code],
                "unit": units.get(unit_code),
                "slug": make_slug(subsection_ru or "osnovnoi", max_length=160),
                "name_ru": subsection_ru or "",
                "subsection_source": subsection_ru or "",
                "has_subsection": bool(has_subsection),
                "polarity": polarity or Polarity.UNKNOWN,
                "observation_count": observation_count,
                "value_count": value_count,
                "no_data_count": no_data_count,
                "hidden_count": hidden_count,
                "region_count": region_count,
                "year_count": year_count,
                "first_year": first_year,
                "last_year": last_year,
                "region_coverage": region_coverage,
                "completeness": completeness,
                "longest_gap_free_span": longest_span,
                "is_analysis_ready": bool(is_analysis_ready),
                "revision_count": revision_count,
            },
        )

    apply_featured_set()
    # Единицы, на которые не ссылается ни один ряд.
    Unit.objects.filter(series__isnull=True).delete()
    return len(rows)


def apply_featured_set() -> None:
    """
    Перенести основной набор в справочник: направленность рядов и ключевые показатели.

    Направленность из набора заменяет угаданную по формулировке. Правка
    ``featured_series.json`` действует со следующей сборкой или ``etl_build --marts``.
    """
    featured = featured_set().by_key()
    for series in Series.objects.filter(key__in=featured).only("id", "key", "polarity"):
        polarity = featured[series.key].polarity
        if series.polarity != polarity:
            series.polarity = polarity
            series.save(update_fields=["polarity"])

    indicator_ids = Series.objects.filter(key__in=featured).values_list("indicator_id")
    Indicator.objects.filter(is_featured=True).exclude(pk__in=indicator_ids).update(
        is_featured=False
    )
    Indicator.objects.filter(pk__in=indicator_ids).update(is_featured=True)


# ---------------------------------------------------------------------------------------
# Методические примечания и разрывы сопоставимости
# ---------------------------------------------------------------------------------------


def sync_methodology(connection: duckdb.DuckDBPyConnection) -> tuple[int, int, int]:
    """
    Разобрать методические примечания и построить разрывы сопоставимости рядов.

    Возвращает число примечаний, связей с рядами и разрывов.
    """
    # Пары «примечание — ряд»: одно примечание сопровождает наблюдения многих рядов.
    # Порядок постоянный: если два примечания отмечают разрыв в один год ряда, остаётся
    # отметка первого, и без порядка от сборки к сборке показывалось бы разное описание.
    pairs = connection.execute(
        """
        SELECT
            m.series_key,
            s.comment,
            count(*) AS occurrence_count
        FROM src        AS s
        JOIN map_series AS m
              ON m.indicator_code = s.indicator_code
             AND m.subsection_raw = coalesce(s.subsection, '')
        WHERE s.comment IS NOT NULL AND s.comment <> 'CD'
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).fetchall()

    # --- Классификация уникальных текстов ------------------------------------------------
    classified: dict[str, Any] = {}
    occurrences: dict[str, int] = defaultdict(int)
    note_series: dict[str, set[str]] = defaultdict(set)

    for series_key, comment, count in pairs:
        classification = classify_note(comment)
        if classification is None:
            continue
        classified.setdefault(classification.checksum, classification)
        occurrences[classification.checksum] += count
        note_series[classification.checksum].add(series_key)

    # --- Запись примечаний ------------------------------------------------------------------
    notes: dict[str, MethodologyNote] = {}
    for checksum, classification in classified.items():
        note, _ = MethodologyNote.objects.update_or_create(
            checksum=checksum,
            defaults={
                "text_ru": classification.text,
                "text_en": note_text_en(checksum),
                "kind": classification.kind,
                "mentioned_years": classification.mentioned_years,
                "affects_comparability": classification.affects_comparability,
                "occurrence_count": occurrences[checksum],
            },
        )
        notes[checksum] = note

    # --- Связи примечаний с рядами -------------------------------------------------------------
    series_by_key = {series.key: series for series in Series.objects.all()}
    link_count = 0
    for checksum, keys in note_series.items():
        note = notes[checksum]
        related = [series_by_key[key] for key in keys if key in series_by_key]
        note.series.set(related)
        link_count += len(related)

    # --- Разрывы сопоставимости ------------------------------------------------------------------
    # Отметки из примечаний пересоздаются целиком. Ряд, значения которого таблица источника
    # заменяет целиком (ВРП), размечается её сносками: примечания набора — о прежних значениях.
    SeriesBreak.objects.all().delete()
    superseded = _superseded_series(connection)

    territories = _territories()
    breaks: list[SeriesBreak] = []
    for checksum, classification in classified.items():
        if not classification.affects_comparability or not classification.breaks:
            continue
        note = notes[checksum]
        for key in sorted(note_series[checksum]):
            series = series_by_key.get(key)
            if series is None or key in superseded:
                continue
            breaks += _series_breaks(series, classification, territories, note=note)

    SeriesBreak.objects.bulk_create(breaks, batch_size=BATCH_SIZE, ignore_conflicts=True)
    sync_source_notes(connection, series_by_key)
    sync_source_breaks(connection, series_by_key)

    _refresh_break_counters()

    # Число записанных отметок: повторы из разных примечаний отбрасывает ограничение
    # уникальности.
    stored = SeriesBreak.objects.count()
    return len(notes), link_count, stored


def sync_source_notes(
    connection: duckdb.DuckDBPyConnection, series_by_key: dict[str, Series]
) -> int:
    """
    Разрывы по сноскам таблицы источника у рядов, значения которых она заменяет целиком.

    Сноски размечаются теми же правилами, что примечания набора.
    """
    if not _has_table(connection, "mart_source_note"):
        return 0
    rows = connection.execute(
        """
        SELECT n.series_key, n.note_text
        FROM mart_source_note AS n
        JOIN mart_source_link AS l
          ON l.series_key = n.series_key AND l.source_code = n.source_code
        WHERE l.mode = 'supersede'
        ORDER BY n.series_key, n.position
        """
    ).fetchall()
    territories = _territories()
    created: list[SeriesBreak] = []
    for key, text in rows:
        series = series_by_key.get(key)
        classification = classify_note(text)
        if series is None or classification is None or not classification.affects_comparability:
            continue
        created += _series_breaks(
            series,
            classification,
            territories,
            description_en=note_text_en(classification.checksum),
        )
    SeriesBreak.objects.bulk_create(created, batch_size=BATCH_SIZE, ignore_conflicts=True)
    return len(created)


def _series_breaks(
    series: Series,
    classification: NoteClassification,
    territories: dict[str, Territory],
    **fields: Any,
) -> list[SeriesBreak]:
    """Отметки ряда по разобранному тексту; вне периода наблюдения ряда отметка не нужна."""
    return [
        SeriesBreak(
            series=series,
            year=item.year,
            kind=item.kind,
            territory=territories.get(item.territory),
            description_ru=classification.text[:1000],
            **fields,
        )
        for item in classification.breaks
        if not (series.first_year and item.year < series.first_year)
        and not (series.last_year and item.year > series.last_year)
    ]


def _territories() -> dict[str, Territory]:
    """Территории справочника по кодам — для отметок, относящихся к одному субъекту."""
    return {territory.code: territory for territory in Territory.objects.all()}


def sync_source_breaks(
    connection: duckdb.DuckDBPyConnection, series_by_key: dict[str, Series]
) -> int:
    """
    Разрывы на стыке набора и внешнего источника при условной связи.

    Условная связь — значения источника за общие годы расходятся с набором больше допуска:
    ряд продолжается, но через стык значения несопоставимы.
    """
    if not _has_table(connection, "mart_source_link"):
        return 0
    rows = connection.execute(
        """
        SELECT l.series_key, l.junction_year, l.source_code, l.within_share,
               l.compared_from, l.compared_to, e.publication_ru, e.publication_en
        FROM mart_source_link AS l
        LEFT JOIN (
            SELECT DISTINCT source_code, publication_ru, publication_en
            FROM dim_edition
            WHERE source_code IS NOT NULL
        ) AS e ON e.source_code = l.source_code
        WHERE l.link = 'conditional' AND l.junction_year IS NOT NULL
        ORDER BY l.series_key, e.publication_ru
        """
    ).fetchall()
    created = []
    for key, year, _source, share, first, last, title_ru, title_en in rows:
        series = series_by_key.get(key)
        if series is None:
            continue
        percent = round((share or 0) * 100)
        created.append(
            SeriesBreak(
                series=series,
                year=year,
                kind=BreakKind.SOURCE,
                description_ru=(
                    f"С {year} года значения — из издания Росстата «{title_ru}». "
                    f"За {first}–{last} годы значения издания и набора совпадают в пределах "
                    f"допуска только у {percent} % пар «субъект × год»: уровни до и после "
                    f"{year} года несопоставимы."
                ),
                description_en=(
                    f"From {year} the values come from Rosstat's «{title_en}». "
                    f"In {first}–{last} its values match the dataset within the tolerance for "
                    f"only {percent}% of region-year pairs, so levels before and after {year} "
                    "are not comparable."
                ),
            )
        )
    SeriesBreak.objects.bulk_create(created, batch_size=BATCH_SIZE, ignore_conflicts=True)
    return len(created)


def _refresh_break_counters() -> None:
    """Обновить число разрывов у каждого ряда."""
    counts: dict[int, int] = defaultdict(int)
    for series_id in SeriesBreak.objects.values_list("series_id", flat=True):
        counts[series_id] += 1

    updated: list[Series] = []
    for series in Series.objects.all().only("id", "break_count"):
        value = counts.get(series.pk, 0)
        if series.break_count != value:
            series.break_count = value
            updated.append(series)

    Series.objects.bulk_update(updated, ["break_count"], batch_size=BATCH_SIZE)


# ---------------------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------------------


def _has_table(connection: duckdb.DuckDBPyConnection, name: str) -> bool:
    """Есть ли в складе таблица: склад прежней сборки может её не содержать."""
    row = connection.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def _superseded_series(connection: duckdb.DuckDBPyConnection) -> set[str]:
    """Ряды, значения которых выпуск источника заменяет целиком (``supersede``)."""
    if not _has_table(connection, "mart_source_link"):
        return set()
    rows = connection.execute(
        "SELECT DISTINCT series_key FROM mart_source_link WHERE mode = 'supersede'"
    ).fetchall()
    return {key for (key,) in rows}


def _free_slugs(model: Any, key_field: str, slugs: dict[str, str]) -> None:
    """
    Освободить адреса записей, у которых адрес меняется.

    Иначе запись, получающая адрес соседки, упёрлась бы в уникальность раньше, чем соседка
    получит свой новый адрес.
    """
    changing = [
        record
        for record in model.objects.filter(**{f"{key_field}__in": list(slugs)}).only(
            "pk", key_field, "slug"
        )
        if record.slug != slugs[getattr(record, key_field)]
    ]
    # Запись, которой в складе больше нет, тоже уступает адрес.
    changing += list(
        model.objects.filter(slug__in=list(slugs.values()))
        .exclude(**{f"{key_field}__in": list(slugs)})
        .only("pk", "slug")
    )
    for record in changing:
        record.slug = f"tmp-{record.pk}"
    model.objects.bulk_update(changing, ["slug"], batch_size=BATCH_SIZE)


def _unique_slug(value: str, used: set[str], *, limit: int) -> str:
    """Построить уникальный слаг; при совпадении названий добавляется порядковый номер."""
    base = make_slug(value, max_length=limit - 6) or "obiekt"
    candidate = base
    counter = 2
    while candidate in used:
        candidate = f"{base}-{counter}"
        counter += 1
    used.add(candidate)
    return candidate
