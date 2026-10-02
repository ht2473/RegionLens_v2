"""
Чтение исходного набора и проверка его схемы, объёмов и состава территорий.

Файл неожиданной структуры дал бы правдоподобные, но неверные расчёты.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from apps.warehouse.duckdb_client import require_row

logger = logging.getLogger(__name__)

# Атрибуты исходного набора и их ожидаемые типы в терминах DuckDB.
EXPECTED_SCHEMA: dict[str, str] = {
    "section": "VARCHAR",
    "indicator_code": "VARCHAR",
    "indicator_name": "VARCHAR",
    "subsection": "VARCHAR",
    "object_name": "VARCHAR",
    "object_level": "VARCHAR",
    "object_oktmo": "VARCHAR",
    "object_okato": "VARCHAR",
    "year": "INTEGER",
    "indicator_value": "DOUBLE",
    "indicator_unit": "VARCHAR",
    "comment": "VARCHAR",
    "source": "VARCHAR",
    "version_date": "VARCHAR",
}

# Значения атрибута object_level в исходных данных.
SOURCE_LEVEL_COUNTRY = "Страна"
SOURCE_LEVEL_DISTRICT = "Федеральный округ"
SOURCE_LEVEL_REGION = "Регион"

# Наименьшие допустимые объёмы: меньше — усечённый файл или ошибка выгрузки.
MIN_OBSERVATIONS = 1_000_000
MIN_TERRITORIES = 90
MIN_INDICATORS = 1_000


@dataclass(slots=True)
class SourceProfile:
    """Результат обследования исходного файла."""

    path: Path
    size_bytes: int
    checksum: str
    observation_count: int
    indicator_count: int
    series_count: int
    territory_count: int
    section_count: int
    edition_count: int
    first_year: int
    last_year: int
    version_code: str
    problems: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Признак пригодности файла к загрузке."""
        return not self.problems

    def as_dict(self) -> dict[str, object]:
        """Представление для журнала запуска ETL."""
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "observation_count": self.observation_count,
            "indicator_count": self.indicator_count,
            "series_count": self.series_count,
            "territory_count": self.territory_count,
            "section_count": self.section_count,
            "edition_count": self.edition_count,
            "first_year": self.first_year,
            "last_year": self.last_year,
            "version_code": self.version_code,
        }


def compute_checksum(path: Path, chunk_size: int = 1 << 20) -> str:
    """Вычислить SHA-256 исходного файла для записи версии набора."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def register_source(connection: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Зарегистрировать исходный parquet как представление ``src`` без копирования в склад."""
    # Параметров в определении представления DuckDB не допускает: путь из настроек
    # подставляется в текст с экранированием кавычек.
    literal = str(path).replace("'", "''")
    connection.execute(f"CREATE OR REPLACE VIEW src AS SELECT * FROM read_parquet('{literal}')")


def profile_source(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
) -> SourceProfile:
    """Обследовать исходный файл и вернуть его профиль вместе со списком замечаний."""
    problems: list[str] = []

    # --- Проверка схемы -------------------------------------------------------------------
    actual_schema = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM src)"
        ).fetchall()
    }

    for column, expected_type in EXPECTED_SCHEMA.items():
        if column not in actual_schema:
            problems.append(f"В наборе отсутствует обязательный атрибут «{column}»")
        elif not types_compatible(actual_schema[column], expected_type):
            problems.append(
                f"Атрибут «{column}» имеет тип {actual_schema[column]}, ожидается {expected_type}"
            )

    unexpected = set(actual_schema) - set(EXPECTED_SCHEMA)
    if unexpected:
        # Новые атрибуты загрузку не ломают.
        logger.warning("Неизвестные атрибуты в источнике: %s", ", ".join(sorted(unexpected)))

    if problems:
        # Дальнейшее обследование бессмысленно: запросы обратятся к отсутствующим колонкам.
        return SourceProfile(
            path=path,
            size_bytes=path.stat().st_size,
            checksum="",
            observation_count=0,
            indicator_count=0,
            series_count=0,
            territory_count=0,
            section_count=0,
            edition_count=0,
            first_year=0,
            last_year=0,
            version_code="",
            problems=problems,
        )

    # --- Сводные характеристики -------------------------------------------------------------
    row = require_row(
        connection.execute(
            """
        SELECT
            count(*)                                       AS observations,
            count(DISTINCT indicator_code)                 AS indicators,
            count(DISTINCT (indicator_code, coalesce(subsection, ''))) AS series,
            count(DISTINCT object_name)                    AS territories,
            count(DISTINCT section)                        AS sections,
            count(DISTINCT source)                         AS editions,
            min(year)                                      AS first_year,
            max(year)                                      AS last_year,
            max(version_date)                              AS version_code
        FROM src
        """
        ).fetchone(),
        "profile_source",
    )

    profile = SourceProfile(
        path=path,
        size_bytes=path.stat().st_size,
        checksum=compute_checksum(path),
        observation_count=row[0],
        indicator_count=row[1],
        series_count=row[2],
        territory_count=row[3],
        section_count=row[4],
        edition_count=row[5],
        first_year=row[6],
        last_year=row[7],
        version_code=row[8] or "",
    )

    # --- Проверка объёмов ----------------------------------------------------------------------
    if profile.observation_count < MIN_OBSERVATIONS:
        problems.append(
            f"Наблюдений {profile.observation_count}, ожидается не менее {MIN_OBSERVATIONS}: "
            "возможно, файл усечён"
        )
    if profile.territory_count < MIN_TERRITORIES:
        problems.append(
            f"Территорий {profile.territory_count}, ожидается не менее {MIN_TERRITORIES}"
        )
    if profile.indicator_count < MIN_INDICATORS:
        problems.append(
            f"Показателей {profile.indicator_count}, ожидается не менее {MIN_INDICATORS}"
        )

    # --- Проверка состава уровней территорий ----------------------------------------------------
    levels = {
        row[0] for row in connection.execute("SELECT DISTINCT object_level FROM src").fetchall()
    }
    for level in (SOURCE_LEVEL_COUNTRY, SOURCE_LEVEL_DISTRICT, SOURCE_LEVEL_REGION):
        if level not in levels:
            problems.append(f"В наборе отсутствует уровень территории «{level}»")

    profile.problems = problems
    return profile


def unmapped_territories(connection: duckdb.DuckDBPyConnection) -> list[str]:
    """Вернуть названия территорий источника, которых нет в справочнике: сборка прерывается."""
    rows = connection.execute(
        """
        SELECT DISTINCT s.object_name
        FROM src AS s
        LEFT JOIN dim_territory AS t ON t.source_name = s.object_name
        WHERE t.territory_code IS NULL
        ORDER BY 1
        """
    ).fetchall()
    return [row[0] for row in rows]


def types_compatible(actual: str, expected: str) -> bool:
    """Сравнить типы с учётом допустимых различий: целые бывают INTEGER, BIGINT, SMALLINT."""
    integer_types = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT"}
    float_types = {"FLOAT", "DOUBLE", "DECIMAL"}

    actual_upper = actual.upper()
    if expected == "INTEGER":
        return actual_upper in integer_types
    if expected == "DOUBLE":
        return actual_upper in float_types or actual_upper.startswith("DECIMAL")
    return actual_upper.startswith(expected)
