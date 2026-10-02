"""
Разобранные выпуски на диске: ``<источник>/<код>_<хэш>.parquet`` со сведениями о выпуске
и сносками таблиц.

Сборка склада читает только эти файлы: база не нужна, всё воспроизводимо из архива.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from django.conf import settings

from .base import PARSED_COLUMNS

_META_KEY = b"regionlens.release"
_NOTES_KEY = b"regionlens.notes"


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    """Сведения о выпуске, записанные вместе с разобранной таблицей."""

    source: str
    code: str
    title: str
    reference_year: int
    published_on: str  # ISO-дата
    fetched_at: str  # ISO-время
    sha256: str
    url: str
    parser_version: int

    @property
    def published(self) -> date:
        """Дата публикации."""
        return date.fromisoformat(self.published_on)

    @property
    def edition_code(self) -> str:
        """Код издания в складе."""
        return f"rel_{self.source}_{self.code}_{self.sha256[:8]}"


def parsed_root() -> Path:
    """Каталог разобранных выпусков."""
    return Path(settings.SOURCE_PARSED_DIR)


def write(info: ReleaseInfo, frame: pd.DataFrame, notes: Sequence[tuple[str, str]] = ()) -> Path:
    """Записать разобранный выпуск и сноски к показателям; запись атомарна."""
    directory = parsed_root() / info.source
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{info.code}_{info.sha256[:8]}.parquet"
    table = pa.Table.from_pandas(frame.reindex(columns=list(PARSED_COLUMNS)), preserve_index=False)
    metadata = dict(table.schema.metadata or {})
    metadata[_META_KEY] = json.dumps(asdict(info), ensure_ascii=False).encode("utf-8")
    if notes:
        payload = json.dumps([list(item) for item in notes], ensure_ascii=False)
        metadata[_NOTES_KEY] = payload.encode("utf-8")
    temporary = target.with_name(target.name + ".part")
    pq.write_table(table.replace_schema_metadata(metadata), temporary)
    temporary.replace(target)
    return target


def remove(source: str, code: str, sha256: str) -> None:
    """Удалить разобранный выпуск, если он есть: разбор не удался."""
    (parsed_root() / source / f"{code}_{sha256[:8]}.parquet").unlink(missing_ok=True)


def read_info(path: Path) -> ReleaseInfo:
    """Сведения о выпуске без чтения таблицы."""
    metadata = pq.read_schema(path).metadata or {}
    payload = json.loads(metadata[_META_KEY].decode("utf-8"))
    return ReleaseInfo(**payload)


def releases(source: str | None = None) -> list[tuple[ReleaseInfo, Path]]:
    """Разобранные выпуски в порядке выхода: от ранних к поздним."""
    root = parsed_root()
    if not root.exists():
        return []
    pattern = f"{source}/*.parquet" if source else "*/*.parquet"
    found = [(read_info(path), path) for path in sorted(root.glob(pattern))]
    return sorted(
        found, key=lambda item: (item[0].reference_year, item[0].published_on, item[0].fetched_at)
    )


def read(path: Path) -> pd.DataFrame:
    """Таблица разобранного выпуска."""
    return pd.read_parquet(path)


def read_notes(path: Path) -> list[tuple[str, str]]:
    """Сноски выпуска к показателям: (показатель, текст)."""
    raw = (pq.read_schema(path).metadata or {}).get(_NOTES_KEY)
    if raw is None:
        return []
    return [(str(measure), str(text)) for measure, text in json.loads(raw.decode("utf-8"))]
