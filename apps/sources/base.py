"""Общее устройство модуля источника: сведения о выпуске и разобранная таблица."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

# Столбцы разобранного выпуска: длинная таблица «показатель × территория × период».
PARSED_COLUMNS = (
    "measure",
    "territory_code",
    "year",
    "period_kind",
    "period",
    "value",
    "hidden",
    "preliminary",
)


@dataclass(frozen=True, slots=True)
class Candidate:
    """Выпуск на сайте источника или в переданном файле."""

    url: str
    release_code: str  # «07-2026», «2026-03-06»
    title: str  # «январь — июль 2026 г.»
    reference_year: int  # год, к которому относится выпуск: порядок изданий в складе
    published_on: date | None = None
    file_name: str = ""


@dataclass(slots=True)
class ParsedRelease:
    """Разобранный выпуск, отчёт о разборе и сноски таблиц."""

    frame: pd.DataFrame
    report: dict[str, Any] = field(default_factory=dict)
    # Сноски к показателям — (показатель, текст): по ним сборка ставит разрывы рядов.
    notes: list[tuple[str, str]] = field(default_factory=list)


class SourceModule(Protocol):
    """
    Модуль источника: где искать выпуски, как их назвать и разобрать.

    Необязательное: ``fetch(candidate) -> bytes`` — выпуск собирается из ответов программного
    интерфейса, а не скачивается файлом; ``collect_history`` — первый сбор берёт все выпуски
    страницы; ``max_per_check`` — предел новых выпусков за проверку (``None`` — без предела).
    """

    code: str
    title_ru: str
    title_en: str
    publisher_ru: str
    publisher_en: str
    short_ru: str
    short_en: str
    licence_ru: str
    licence_en: str
    page_url: str
    check_every: timedelta
    parser_version: int

    def discover(self) -> list[Candidate]:
        """Выпуски на сайте источника, новые первыми."""
        ...

    def identify(self, file_name: str, url: str = "") -> Candidate:
        """Выпуск по имени файла, переданного вручную."""
        ...

    def parse(self, files: Path) -> ParsedRelease:
        """Разобрать распакованный выпуск; неожиданный вид — ``SheetFormatError``."""
        ...


def empty_frame() -> pd.DataFrame:
    """Пустая таблица разобранного выпуска с нужными столбцами."""
    return pd.DataFrame({column: pd.Series(dtype="object") for column in PARSED_COLUMNS})
