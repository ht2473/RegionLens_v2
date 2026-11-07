"""Контекст сборки образа: данные, которых нет в репозитории, не попадают и в образ."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.unit


def _patterns(name: str) -> set[str]:
    """Строки правил файла исключений без комментариев и пустых строк."""
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def test_ignored_data_directories_stay_out_of_the_image() -> None:
    """
    Каталог данных вне git исключён и из образа.

    Образ собирается из рабочей копии разработчика (scripts/deploy.sh), а том стенда при
    первом подключении заполняется содержимым образа: загруженные в разработке таблицы,
    архив выпусков и склад иначе уехали бы на сервер.
    """
    directories = {
        pattern
        for pattern in _patterns(".gitignore")
        if pattern.startswith("data/") and pattern.endswith("/") and pattern.count("/") == 2
    }
    assert directories, "в .gitignore нет каталогов данных"
    missing = directories - _patterns(".dockerignore")
    assert not missing, f"в образ попадут: {sorted(missing)}"


def test_volume_mount_points_exist_in_the_image() -> None:
    """
    Каждая точка подключения тома приложения создаётся в образе.

    Пустой том при первом подключении получает владельца каталога из образа; без каталога
    том принадлежит root, и служба без прав root падает на первой записи.
    """
    compose = (ROOT / "docker" / "compose.prod.yml").read_text(encoding="utf-8")
    mounts = set(re.findall(r"^\s*- [a-z_]+:(/app/[a-z_/]+)", compose, flags=re.MULTILINE))
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    created = set(
        re.findall(r"/app/[a-z_/]+", dockerfile.split("RUN mkdir -p", 1)[1].split("&&")[0])
    )
    assert mounts, "в описании служб нет томов приложения"
    missing = mounts - created
    assert not missing, f"не создаются в образе: {sorted(missing)}"
