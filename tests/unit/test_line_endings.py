"""Окончания строк LF в файлах, исполняемых Linux: сценарий с CRLF в контейнере не запускается."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

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
