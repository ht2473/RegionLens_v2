"""
Сравнение склада, собранного из новой версии набора, с рабочим: что изменится, если её принять.

Рабочий склад подключается только на чтение; сравниваются ряды, значения, единицы,
связи с выпусками источников и методические примечания набора.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import duckdb

from apps.warehouse.duckdb_client import require_row

from .classify import classify_note

# Под этим именем рабочий склад подключается к соединению кандидата.
CURRENT = "current_warehouse"

# Значения считаются разными, если расходятся больше этой доли (не меньше единицы).
SAME_VALUE = 1e-9

# С какого сходства разрезов исчезнувший и новый ряд одного показателя — переименование.
RENAME_SIMILARITY = 0.85

# Сколько примеров каждого вида показывать.
SAMPLE = 25
NOTE_SAMPLE = 10
NOTE_LENGTH = 300

# Латинские буквы, неотличимые от кириллических: встречаются в названиях разрезов.
_LOOKALIKES = str.maketrans("aceopxyACEHKMOPTXY", "асеорхуАСЕНКМОРТХУ")
_SPACING = re.compile(r"[\s\-‐‑–—]+")


def compare(
    connection: duckdb.DuckDBPyConnection, current_path: Path, current_source: Path | None
) -> dict[str, Any]:
    """Отчёт о различиях; без рабочего склада — только итоги кандидата."""
    report: dict[str, Any] = {"candidate": _totals(connection, ""), "current": None}
    if current_path.exists():
        literal = str(current_path).replace("'", "''")
        connection.execute(f"ATTACH '{literal}' AS {CURRENT} (READ_ONLY)")
        try:
            report["current"] = _totals(connection, f"{CURRENT}.")
            report["series"] = _series(connection)
            report["values"] = _values(connection)
            report["units"] = _units(connection)
            report["links"] = _links(connection)
        finally:
            connection.execute(f"DETACH {CURRENT}")
    if current_source is not None and current_source.exists():
        report["notes"] = _notes(connection, current_source)
    return report


def summary_lines(report: dict[str, Any]) -> list[str]:
    """Главное из отчёта строками — для письма администратору."""
    lines: list[str] = []
    candidate, current = report["candidate"], report.get("current")
    if current is None:
        lines.append(f"Версия {candidate['version']}; рабочего склада нет, сравнивать не с чем.")
        return lines

    lines.append(f"Версия: {current['version']} → {candidate['version']}")
    series = report["series"]
    lines.append(
        f"Рядов: {_number(current['series'])} → {_number(candidate['series'])} "
        f"(новых {series['added']}, исчезло {series['removed']}, "
        f"переименовано {len(series['renamed'])})"
    )
    values = report["values"]
    lines.append(
        f"Значений: {_number(current['values'])} → {_number(candidate['values'])}; "
        f"изменилось {_number(values['changed'])}, появилось {_number(values['appeared'])}, "
        f"пропало {_number(values['lost'])}"
    )
    lines.append(
        f"Последний год: {current['last_year']} → {candidate['last_year']}; "
        f"рядов продлено {values['extended']}, укорочено {values['shortened']}"
    )
    if report["units"]["count"]:
        lines.append(f"Сменилась единица измерения: {report['units']['count']}")
    if report["links"]:
        lines.append(f"Связи с выпусками источников изменились: {len(report['links'])}")
    notes = report.get("notes")
    if notes:
        lines.append(
            f"Новых методических примечаний: {notes['count']}, "
            f"из них отмечают разрыв: {notes['breaks']}"
        )
    references = report.get("references", [])
    if references:
        project = sum(1 for item in references if item["origin"] not in {"saved", "favorite"})
        lines.append(
            f"Ссылки на исчезнувшие ряды: в справочниках проекта — {project}, "
            f"в сохранённом пользователями — {len(references) - project}"
        )
    return lines


def _totals(connection: duckdb.DuckDBPyConnection, prefix: str) -> dict[str, Any]:
    """Версия набора и объёмы склада."""
    row = connection.execute(
        f"""
        SELECT
            (SELECT value FROM {prefix}meta_build WHERE key = 'dataset_version'),
            (SELECT count(*) FROM {prefix}dim_series),
            (SELECT count(*) FROM {prefix}fact_observation),
            (SELECT count(*) FROM {prefix}fact_observation WHERE value IS NOT NULL),
            (SELECT max(year) FROM {prefix}fact_observation WHERE value IS NOT NULL)
        """
    ).fetchone()
    version, series, observations, values, last_year = require_row(row, "итоги склада")
    return {
        "version": version or "",
        "series": int(series),
        "observations": int(observations),
        "values": int(values),
        "last_year": last_year,
    }


def _series(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Новые, исчезнувшие и переименованные ряды."""
    query = (
        "SELECT series_key, indicator_code, coalesce(subsection_ru, ''), full_title_ru "
        "FROM {}dim_series"
    )
    candidate = {row[0]: row[1:] for row in connection.execute(query.format("")).fetchall()}
    current = {
        row[0]: row[1:] for row in connection.execute(query.format(f"{CURRENT}.")).fetchall()
    }
    added = sorted(set(candidate) - set(current))
    removed = sorted(set(current) - set(candidate))

    renamed = []
    paired_new: set[str] = set()
    for old in removed:
        indicator, subsection, title = current[old]
        best, score = "", 0.0
        for new in added:
            if new in paired_new or candidate[new][0] != indicator:
                continue
            similarity = SequenceMatcher(
                None, _plain(subsection), _plain(candidate[new][1])
            ).ratio()
            if similarity > score:
                best, score = new, similarity
        if best and score >= RENAME_SIMILARITY:
            paired_new.add(best)
            renamed.append(
                {
                    "before": old,
                    "after": best,
                    "title_before": title,
                    "title_after": candidate[best][2],
                    "same_text": score == 1.0,
                }
            )
    paired_old = {item["before"] for item in renamed}
    return {
        "added": len(added),
        "removed": len(removed),
        "renamed": renamed,
        "added_sample": [
            {"key": key, "title": candidate[key][2]} for key in added if key not in paired_new
        ][:SAMPLE],
        "removed_sample": [
            {"key": key, "title": current[key][2]} for key in removed if key not in paired_old
        ][:SAMPLE],
    }


def _values(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Изменённые, появившиеся и пропавшие значения; ряды, продлённые и укороченные по годам."""
    differs = "abs(new.value - old.value) > ? * greatest(1, abs(old.value))"
    row = connection.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE new.value IS NOT NULL AND old.value IS NOT NULL AND {differs}),
            count(*) FILTER (WHERE new.value IS NOT NULL AND old.value IS NULL),
            count(*) FILTER (WHERE new.value IS NULL AND old.value IS NOT NULL)
        FROM fact_observation AS new
        FULL JOIN {CURRENT}.fact_observation AS old USING (series_key, territory_code, year)
        """,
        [SAME_VALUE],
    ).fetchone()
    changed, appeared, lost = (int(value) for value in require_row(row, "значения"))

    top = connection.execute(
        f"""
        SELECT new.series_key, any_value(s.full_title_ru), count(*) AS changed
        FROM fact_observation AS new
        JOIN {CURRENT}.fact_observation AS old USING (series_key, territory_code, year)
        JOIN dim_series AS s ON s.series_key = new.series_key
        WHERE {differs}
        GROUP BY 1
        ORDER BY 3 DESC, 1
        LIMIT 10
        """,
        [SAME_VALUE],
    ).fetchall()

    lengths = connection.execute(
        f"""
        WITH
            new AS (
                SELECT series_key, max(year) AS last_year FROM fact_observation
                WHERE value IS NOT NULL GROUP BY 1
            ),
            old AS (
                SELECT series_key, max(year) AS last_year FROM {CURRENT}.fact_observation
                WHERE value IS NOT NULL GROUP BY 1
            )
        SELECT
            count(*) FILTER (WHERE new.last_year > old.last_year),
            count(*) FILTER (WHERE new.last_year < old.last_year)
        FROM new JOIN old USING (series_key)
        """
    ).fetchone()
    extended, shortened = require_row(lengths, "длины рядов")
    return {
        "changed": changed,
        "appeared": appeared,
        "lost": lost,
        "extended": int(extended),
        "shortened": int(shortened),
        "top": [{"key": key, "title": title, "changed": int(count)} for key, title, count in top],
    }


def _units(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Ряды, у которых сменилась единица измерения."""
    rows = connection.execute(
        f"""
        SELECT new.series_key, new.full_title_ru, old.unit_code, new.unit_code
        FROM dim_series AS new
        JOIN {CURRENT}.dim_series AS old USING (series_key)
        WHERE new.unit_code IS DISTINCT FROM old.unit_code
        ORDER BY 1
        """
    ).fetchall()
    return {
        "count": len(rows),
        "sample": [
            {"key": key, "title": title, "before": before or "", "after": after or ""}
            for key, title, before, after in rows[:SAMPLE]
        ],
    }


def _links(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Связи с выпусками источников, которые появились, пропали или сменили вид."""
    rows = connection.execute(
        f"""
        SELECT series_key, old.link, new.link, old.within_share, new.within_share
        FROM mart_source_link AS new
        FULL JOIN {CURRENT}.mart_source_link AS old USING (series_key)
        WHERE new.link IS DISTINCT FROM old.link
        ORDER BY 1
        """
    ).fetchall()
    return [
        {
            "key": key,
            "before": before or "",
            "after": after or "",
            "share_before": share_before,
            "share_after": share_after,
        }
        for key, before, after, share_before, share_after in rows
    ]


def _notes(connection: duckdb.DuckDBPyConnection, current_source: Path) -> dict[str, Any]:
    """Тексты примечаний, которых не было в действующем наборе, и сколько из них — разрывы."""
    literal = str(current_source).replace("'", "''")
    texts = [
        row[0]
        for row in connection.execute(
            f"""
            SELECT DISTINCT comment FROM src WHERE comment IS NOT NULL
            EXCEPT
            SELECT DISTINCT comment FROM read_parquet('{literal}') WHERE comment IS NOT NULL
            ORDER BY 1
            """
        ).fetchall()
    ]
    classified = [(text, classify_note(text)) for text in texts]
    notes = [(text, item) for text, item in classified if item is not None]
    breaks = [(text, item) for text, item in notes if item.breaks]
    return {
        "count": len(notes),
        "breaks": len(breaks),
        "sample": [
            {"text": text[:NOTE_LENGTH], "years": item.break_years}
            for text, item in (breaks + [pair for pair in notes if not pair[1].breaks])[
                :NOTE_SAMPLE
            ]
        ],
    }


def _plain(text: str) -> str:
    """Разрез без различий набора: регистр, пробелы, дефисы и переносы, «ё», латиница."""
    return _SPACING.sub("", text.translate(_LOOKALIKES).lower().replace("ё", "е"))


def _number(value: int) -> str:
    return f"{value:,}".replace(",", " ")
