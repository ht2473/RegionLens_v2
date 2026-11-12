"""
Файлы, исполняемые Linux: окончания строк LF (сценарий с CRLF в контейнере не запускается)
и право на запуск в репозитории (образ собирается из архива коммита, таймеры systemd
вызывают сценарии напрямую).
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 — только git с постоянными аргументами
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Запускаются напрямую: точка входа образа, сценарии стенда. postgres-init.sh образ
# PostgreSQL подключает через source — право на запуск ему не нужно.
RUN_DIRECTLY = [
    "docker/entrypoint.sh",
    *sorted(f"scripts/{p.name}" for p in (ROOT / "scripts").glob("*.sh")),
]

EXECUTED_BY_LINUX = [
    ROOT / "docker" / "entrypoint.sh",
    ROOT / "docker" / "Caddyfile",
    *sorted((ROOT / "scripts").glob("*.sh")),
    *sorted((ROOT / "deploy" / "systemd").glob("*")),
]


@pytest.mark.unit
@pytest.mark.parametrize("path", EXECUTED_BY_LINUX, ids=lambda path: path.name)
def test_file_uses_unix_line_endings(path: Path) -> None:
    """Файл, который читает или исполняет Linux, записан с окончаниями LF."""
    assert b"\r\n" not in path.read_bytes()


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="нет git")
def test_scripts_are_executable_in_repository() -> None:
    """Сценарии, которые запускаются напрямую, хранятся в git с правом на запуск."""
    listing = subprocess.run(  # nosec B603 B607 — git из PATH с постоянными аргументами
        ["git", "ls-files", "-s", "--", *RUN_DIRECTLY],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    modes = {line.split("\t")[1]: line.split()[0] for line in listing.splitlines()}
    assert set(modes) == set(RUN_DIRECTLY)
    assert {path for path, mode in modes.items() if mode != "100755"} == set()
