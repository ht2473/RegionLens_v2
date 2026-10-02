"""
Сборка отчётов из данных склада — одним обращением на таблицу.

Каждый отчёт несёт реквизиты: источник, версию набора и дату формирования.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.conf import settings
from django.utils import timezone, translation

from apps.catalog.constants import ValueFlag
from apps.catalog.models import Territory
from apps.catalog.passport import Position, build_passport
from apps.catalog.provenance import origin_of_year, release_phrase, source_title
from apps.catalog.status import series_status
from apps.catalog.versions import current_version_code
from apps.sources.collect import SOURCES
from apps.sources.registry import registry
from apps.warehouse.queries import (
    available_years,
    featured_set,
    ranked_years,
    ranking_table,
    region_directory,
    region_panel,
    series_metadata,
    territory_coverage,
    territory_profile,
)
from apps.warehouse.queries.sources import source_years

from ..constants import ReportKind
from .base import (
    COLUMN_INTEGER,
    COLUMN_NUMBER,
    COLUMN_TEXT,
    Column,
    ReportDocument,
    Section,
    Table,
)


class ReportParameterError(ValueError):
    """Параметры задания не позволяют собрать отчёт."""


def build_report(kind: str, parameters: dict[str, Any], *, max_rows: int) -> ReportDocument:
    """Собрать отчёт указанного вида по его параметрам."""
    builders = {
        ReportKind.SERIES: build_series_report,
        ReportKind.TERRITORY: build_territory_report,
        ReportKind.RANKING: build_ranking_report,
    }
    builder = builders.get(ReportKind(kind))
    if builder is None:  # pragma: no cover - вид ограничен перечислением
        raise ReportParameterError(f"Неизвестный вид отчёта: {kind}")
    return builder(parameters, max_rows=max_rows)


# ---------------------------------------------------------------------------------------
# Отчёт по ряду наблюдений
# ---------------------------------------------------------------------------------------


def build_series_report(parameters: dict[str, Any], *, max_rows: int) -> ReportDocument:
    """Собрать таблицу «регионы × годы» по одному ряду наблюдений; пропуски — пропусками."""
    series_key = str(parameters.get("series", ""))
    metadata = series_metadata(series_key)
    if metadata is None:
        raise ReportParameterError("Ряд наблюдений не найден")

    panel = region_panel(series_key)
    years = sorted(panel)
    first_year = _as_int(parameters.get("first_year"))
    last_year = _as_int(parameters.get("last_year"))
    if first_year:
        years = [year for year in years if year >= first_year]
    if last_year:
        years = [year for year in years if year <= last_year]
    if not years:
        raise ReportParameterError("За выбранный период значений нет")

    regions = region_directory()[:max_rows]

    columns = [Column("territory", "Субъект Российской Федерации", COLUMN_TEXT, width=42)]
    columns += [Column(str(year), str(year), COLUMN_NUMBER, width=12) for year in years]

    rows: list[dict[str, Any]] = []
    filled = 0
    for region in regions:
        row: dict[str, Any] = {"territory": region["name_ru"]}
        for year in years:
            value = panel.get(year, {}).get(region["territory_code"])
            row[str(year)] = value
            filled += value is not None
        rows.append(row)

    total_cells = len(regions) * len(years)
    coverage = filled / total_cells if total_cells else 0.0
    counts = {
        year: sum(1 for value in panel.get(year, {}).values() if value is not None)
        for year in years
    }
    origins = _value_sources(series_key, years, counts)

    document = ReportDocument(
        title=metadata["full_title_ru"],
        subtitle=f"Значения по субъектам Российской Федерации, {years[0]}—{years[-1]}",
        meta=_meta_block(
            [
                ("Единица измерения", metadata.get("unit_name_ru") or "не указана"),
                ("Годы", f"{years[0]}—{years[-1]}"),
                ("Субъектов в таблице", str(len(regions))),
                ("Заполненность", f"{coverage * 100:.1f} %"),
                *_status_row(series_key),
            ],
            **_sources_of(series_key),
        ),
        sections=[
            Section(
                heading="Значения показателя",
                paragraphs=[
                    "Прочерк означает отсутствие наблюдения за год, а не нулевое значение. "
                    "Строки соответствуют субъектам Российской Федерации без составных "
                    "территорий: их включение привело бы к двойному учёту.",
                    *([origins] if origins else []),
                ],
                tables=[
                    Table(
                        columns=columns,
                        rows=rows,
                        title=metadata["full_title_ru"],
                        note=_coverage_note(metadata),
                    )
                ],
            )
        ],
        footer=_footer(),
    )
    return document


# ---------------------------------------------------------------------------------------
# Паспорт региона
# ---------------------------------------------------------------------------------------


def build_territory_report(parameters: dict[str, Any], *, max_rows: int) -> ReportDocument:
    """
    Собрать паспорт субъекта из тех же выводов, что на странице (``apps.catalog.passport``).

    Документ русскоязычный при любом языке страницы.
    """
    code = str(parameters.get("territory", ""))
    territory = Territory.objects.filter(code=code).first()
    profile = territory_profile(code)
    if territory is None or profile is None:
        raise ReportParameterError("Территория не найдена")

    coverage = territory_coverage(code)
    with translation.override("ru"):
        passport = build_passport(code)
        summary = passport.summary
        rank_rows = [_position_row(position) for position in passport.positions[:max_rows]]
        strength_rows = [_position_row(position) for position in passport.strengths]
        weakness_rows = [_position_row(position) for position in passport.weaknesses]
        stopped = _discontinued_paragraph(passport.discontinued)
        origins = _origin_paragraph(passport.positions[:max_rows])
        external = _external_sources(passport.positions[:max_rows])

    rank_columns = [
        Column("theme", "Тема", COLUMN_TEXT, width=20),
        Column("title", "Показатель", COLUMN_TEXT, width=40),
        Column("year", "Год", COLUMN_INTEGER, width=8),
        Column("value", "Значение", COLUMN_NUMBER, width=14),
        Column("unit", "Единица", COLUMN_TEXT, width=18),
        Column("rank", "Место", COLUMN_INTEGER, width=8),
        Column("of", "Из", COLUMN_INTEGER, width=8),
        Column("standing", "Положение", COLUMN_TEXT, width=30),
        Column("versus", "С Россией", COLUMN_TEXT, width=28),
        Column("trend", "За пять лет", COLUMN_TEXT, width=40),
    ]

    sections = [
        Section(
            heading="Главное",
            paragraphs=[
                f"Федеральный округ: {territory.parent.name_ru if territory.parent else '—'}.",
                f"Административный центр: {territory.capital_ru or '—'}.",
                *summary,
                (
                    "Полнота данных: значения есть по "
                    f"{coverage.get('series_with_values', 0)} рядам из "
                    f"{coverage.get('series_total', 0)}, пригодных для анализа."
                ),
            ],
        ),
        Section(
            heading="Сильные стороны",
            paragraphs=[
                "Показатели основного набора, по которым субъект входит в число 15 % лучших "
                "с учётом направленности показателя.",
            ],
            tables=[
                Table(columns=_strength_columns(), rows=strength_rows, title="Сильные стороны")
            ],
        ),
        Section(
            heading="Слабые стороны",
            paragraphs=[
                "Показатели основного набора, по которым субъект входит в число 15 % худших.",
            ],
            tables=[Table(columns=_strength_columns(), rows=weakness_rows, title="Слабые стороны")],
        ),
        Section(
            heading="Основные показатели",
            paragraphs=[
                "Для каждого показателя взят последний год, за который рассчитано место. "
                "Первое место — лучшее с учётом направленности; у показателей без оценки "
                "и у величин, зависящих от размера региона, — наибольшее значение. "
                "Изменение за пять лет не приводится, если в этот срок источник менял "
                "методику или круг наблюдения. Изменение денежных показателей названо "
                "реальным — за вычетом роста цен, а в скобках — в рублях.",
                *stopped,
                *origins,
            ],
            tables=[Table(columns=rank_columns, rows=rank_rows, title="Основные показатели")],
        ),
    ]

    return ReportDocument(
        title=f"Паспорт региона: {territory.name_ru}",
        subtitle="Основные показатели и положение среди регионов",
        meta=_meta_block(
            [
                ("Код субъекта", territory.code),
                ("Федеральный округ", territory.parent.name_ru if territory.parent else "—"),
                ("Показателей в отчёте", str(len(rank_rows))),
            ],
            external=external,
        ),
        sections=sections,
        footer=_footer(),
    )


def _discontinued_paragraph(positions: list[Position]) -> list[str]:
    """Абзац о показателях, публикацию которых источник прекратил; пусто — таких нет."""
    by_year: dict[int, list[str]] = {}
    for position in positions:
        if position.status is not None and position.status.since is not None:
            by_year.setdefault(position.status.since, []).append(
                position.series.short_title_ru.lower()
            )
    if not by_year:
        return []
    parts = [f"в {year} году — {', '.join(titles)}" for year, titles in sorted(by_year.items())]
    return [
        "Росстат прекратил публикацию показателей: "
        + "; ".join(parts)
        + ". Их значения больше не обновятся."
    ]


def _position_row(position: Position) -> dict[str, Any]:
    """Строка таблицы паспорта: значение, место и выводы словами."""
    theme = featured_set().theme(position.series.theme)
    return {
        "theme": theme.title_ru if theme else "",
        "title": position.series.short_title_ru,
        "year": position.year,
        "value": position.value,
        "unit": position.series.unit_label_ru,
        "rank": position.place,
        "of": position.territories,
        "standing": position.standing,
        "versus": position.versus_country,
        "trend": position.trend,
    }


# ---------------------------------------------------------------------------------------
# Рейтинг регионов
# ---------------------------------------------------------------------------------------


def build_ranking_report(parameters: dict[str, Any], *, max_rows: int) -> ReportDocument:
    """Собрать рейтинг субъектов по ряду за год с изменением позиции."""
    series_key = str(parameters.get("series", ""))
    metadata = series_metadata(series_key)
    if metadata is None:
        raise ReportParameterError("Ряд наблюдений не найден")

    years = ranked_years(series_key) or available_years(series_key)
    if not years:
        raise ReportParameterError("Рейтинг по этому ряду не рассчитан")

    year = _as_int(parameters.get("year")) or years[-1]
    if year not in years:
        year = years[-1]
    previous_year = next((item for item in reversed(years) if item < year), None)
    ascending = bool(parameters.get("ascending"))

    rows_raw = ranking_table(
        series_key,
        year,
        previous_year=previous_year,
        ascending=ascending,
    )[:max_rows]

    columns = [
        Column("rank", "Место", COLUMN_INTEGER, width=8),
        Column("territory", "Субъект", COLUMN_TEXT, width=40),
        Column("district", "Федеральный округ", COLUMN_TEXT, width=28),
        Column("value", "Значение", COLUMN_NUMBER, width=16),
        Column("ratio", "К среднему по стране", COLUMN_NUMBER, width=16),
        Column("change", "Изменение места", COLUMN_INTEGER, width=14),
    ]

    rank_key = "rank_asc" if ascending else "rank_desc"
    previous_key = "previous_rank_asc" if ascending else "previous_rank_desc"

    rows = [
        {
            "rank": row[rank_key],
            "territory": row["name_ru"],
            "district": row.get("district_name") or "—",
            "value": row["value"],
            "ratio": row.get("ratio_to_country"),
            "change": (
                row[previous_key] - row[rank_key]
                if row.get(previous_key) is not None and row.get(rank_key) is not None
                else None
            ),
        }
        for row in rows_raw
    ]

    direction = "по возрастанию значения" if ascending else "по убыванию значения"
    return ReportDocument(
        title=f"Рейтинг субъектов: {metadata['full_title_ru']}",
        subtitle=f"{year} год, {direction}",
        meta=_meta_block(
            [
                ("Единица измерения", metadata.get("unit_name_ru") or "не указана"),
                ("Год", str(year)),
                ("Год сравнения", str(previous_year) if previous_year else "—"),
                ("Субъектов в рейтинге", str(len(rows))),
                *_origin_row(series_key, year),
                *_status_row(series_key),
            ],
            **_sources_of(series_key),
        ),
        sections=[
            Section(
                heading="Рейтинг",
                paragraphs=[
                    "Положительное изменение места означает подъём в рейтинге "
                    "относительно года сравнения.",
                ],
                tables=[
                    Table(
                        columns=columns,
                        rows=rows,
                        title=metadata["full_title_ru"],
                        note=_coverage_note(metadata),
                    )
                ],
            )
        ],
        footer=_footer(),
    )


# ---------------------------------------------------------------------------------------
# Общие части
# ---------------------------------------------------------------------------------------


def _strength_columns() -> list[Column]:
    """Столбцы таблицы сильных и слабых сторон."""
    return [
        Column("title", "Показатель", COLUMN_TEXT, width=40),
        Column("year", "Год", COLUMN_INTEGER, width=8),
        Column("value", "Значение", COLUMN_NUMBER, width=14),
        Column("unit", "Единица", COLUMN_TEXT, width=18),
        Column("rank", "Место", COLUMN_INTEGER, width=8),
        Column("of", "Из", COLUMN_INTEGER, width=8),
        Column("versus", "С Россией", COLUMN_TEXT, width=28),
    ]


def _status_row(series_key: str) -> list[tuple[str, str]]:
    """Реквизит «Публикация» по справочнику состояния рядов; нет сведений — нет строки."""
    status = series_status(series_key)
    if status is None:
        return []
    with translation.override("ru"):
        return [("Публикация", f"{status.label.capitalize()}. {status.detail}")]


def _origin_row(series_key: str, year: int) -> list[tuple[str, str]]:
    """Реквизит «Значения года», если они не из набора: издание, признаки, способ расчёта."""
    with translation.override("ru"):
        origin = origin_of_year(series_key, year)
        if origin is None:
            return []
        return [("Значения года", origin.text)]


def _coverage_note(metadata: dict[str, Any]) -> str:
    """Примечание о покрытии ряда."""
    coverage = metadata.get("region_coverage")
    completeness = metadata.get("completeness")
    if coverage is None or completeness is None:
        return ""
    return (
        f"Ряд охватывает {coverage * 100:.0f} % субъектов; "
        f"заполненность наблюдений — {completeness * 100:.0f} %."
    )


def _meta_block(
    extra: list[tuple[str, str]],
    *,
    dataset: bool = True,
    external: list[str] | tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """
    Собрать блок реквизитов: источники значений с условиями использования и дату отчёта.

    ``dataset`` — в отчёте есть значения набора (его обработка и лицензия CC BY указываются
    всегда, когда он источник); ``external`` — коды внешних источников.
    """
    rows = list(extra)
    if dataset:
        rows += [
            ("Источник данных", f"{settings.DATA_SOURCE_ORIGIN}, {settings.DATA_SOURCE_TITLE}"),
            ("Обработка", settings.DATA_SOURCE_PROCESSOR),
            ("Версия набора", current_version_code()),
            ("Лицензия", settings.DATA_SOURCE_LICENSE),
        ]
    for index, code in enumerate(external):
        module = SOURCES.get(code)
        if module is None:
            continue
        label = "Источник данных" if not dataset and index == 0 else "Также"
        rows.append((label, f"{module.publisher_ru}, «{module.title_ru}»; {module.licence_ru}"))
    rows.append(("Отчёт сформирован", timezone.localtime().strftime("%d.%m.%Y %H:%M")))
    return rows


def _sources_of(series_key: str) -> dict[str, Any]:
    """Источники значений ряда для реквизитов: набор и внешние источники."""
    external = sorted({row["source_code"] for row in source_years(series_key)})
    return {"dataset": series_key not in registry().by_key, "external": external}


def _external_sources(positions: list[Position]) -> list[str]:
    """Внешние источники значений в таблице паспорта."""
    found: set[str] = set()
    for position in positions:
        origin = position.origin
        if origin is not None:
            found.add(origin.source_code)
    return sorted(found)


def _value_sources(series_key: str, years: list[int], counts: dict[int, int]) -> str:
    """
    Сноска об источниках значений по годам: набор, выпуски Росстата, Банка России, ФНС.

    Год, в котором выпуск дал значения не всех субъектов, отмечается их числом.
    """
    rows = [row for row in source_years(series_key) if row["year"] in years]
    if not rows:
        return ""
    # Выпуск → пометка года → годы: годы с одной пометкой сворачиваются в отрезки.
    releases: dict[tuple[str, str, Any], dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    external: dict[int, int] = defaultdict(int)
    with translation.override("ru"):
        for row in rows:
            year = int(row["year"])
            values = int(row["values"])
            external[year] += values
            notes = []
            if values < counts.get(year, 0):
                notes.append(f"{values} субъектов")
            if ValueFlag.PRELIMINARY in ValueFlag(int(row["flags"] or 0)):
                notes.append("предварительные")
            key = (row["source_code"], row["edition_label"], row.get("released_on"))
            releases[key][", ".join(notes)].append(year)
        parts = []
        dataset_years = [year for year in years if counts.get(year, 0) > external.get(year, 0)]
        if dataset_years:
            parts.append(f"набор «Если быть точным» — {_span(dataset_years)}")
        for (code, label, released), groups in releases.items():
            items = [
                _span(group) + (f" ({note})" if note else "") for note, group in groups.items()
            ]
            parts.append(
                f"{source_title(code)}, {release_phrase(label, released)} — {'; '.join(items)}"
            )
    return "Источники значений: " + "; ".join(parts) + "."


def _span(years: list[int]) -> str:
    """Годы отрезками: «2001–2023, 2025»."""
    runs: list[list[int]] = []
    for year in sorted(set(years)):
        if runs and year == runs[-1][-1] + 1:
            runs[-1].append(year)
        else:
            runs.append([year])
    return ", ".join(f"{run[0]}–{run[-1]}" if len(run) > 1 else str(run[0]) for run in runs)


def _lower_first(text: str) -> str:
    """Строчная первая буква, кроме сокращений: «ВРП на душу» остаётся как есть."""
    if len(text) > 1 and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def _origin_paragraph(positions: list[Position]) -> list[str]:
    """Абзац о значениях не из набора: по источникам, с годами и пометками."""
    groups: dict[str, list[str]] = defaultdict(list)
    with translation.override("ru"):
        for position in positions:
            origin = position.origin
            if origin is None:
                continue
            notes = [str(position.year)]
            if origin.is_preliminary:
                notes.append("предварительные")
            elif origin.is_computed:
                notes.append("расчёт RegionLens")
            groups[origin.source_text].append(
                f"{_lower_first(position.series.short_title_ru)} ({', '.join(notes)})"
            )
    if not groups:
        return []
    listed = "; ".join(f"{source}: {', '.join(items)}" for source, items in groups.items())
    return [f"Значения не из набора «Если быть точным». {listed}."]


def _footer() -> str:
    """Колонтитул отчёта: приложение и автор работы; адрес сайта добавляет выдача."""
    return f"{settings.PROJECT_NAME} {settings.PROJECT_VERSION} · {settings.PROJECT_AUTHOR}"


def _as_int(value: Any) -> int | None:
    """Привести значение параметра к целому, не падая на мусоре."""
    try:
        return int(value)
    except TypeError, ValueError:
        return None
