"""
Распаковка выпусков в ZIP и RAR во временный каталог.

ZIP Росстата пишет имена в кодировке cp866 без флага UTF-8; RAR читает bsdtar (libarchive):
в Windows 11 это системный ``tar.exe``, на сервере — пакет ``libarchive-tools``.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess  # запускается только bsdtar с путями из архива сборщика
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings

# Флаг ZIP «имя в UTF-8».
_UTF8_FLAG = 0x800
_WINDOWS_BSDTAR = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tar.exe"


class UnpackError(RuntimeError):
    """Архив выпуска не распаковывается."""


def unpack(archive: Path, target: Path) -> Path:
    """Распаковать ZIP или RAR в ``target``; файл другого вида копируется как есть."""
    target.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        _unzip(archive, target)
    elif suffix == ".rar":
        _unrar(archive, target)
    else:
        shutil.copy2(archive, target / archive.name)
    return target


def bsdtar() -> str | None:
    """Путь к bsdtar: из настроек, из PATH или системный tar.exe Windows."""
    if settings.SOURCE_BSDTAR:
        return str(settings.SOURCE_BSDTAR)
    found = shutil.which("bsdtar")
    if found:
        return found
    if os.name == "nt" and _WINDOWS_BSDTAR.exists():
        return str(_WINDOWS_BSDTAR)
    return None


def _unzip(archive: Path, target: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                name = info.filename
                if not info.flag_bits & _UTF8_FLAG:
                    with contextlib.suppress(UnicodeError):
                        name = name.encode("cp437").decode("cp866")
                relative = _safe_relative(name)
                if relative is None:
                    continue
                destination = target / relative
                if name.endswith("/"):
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(bundle.read(info))
    except zipfile.BadZipFile as error:
        raise UnpackError(f"{archive.name}: повреждённый ZIP ({error})") from error


def _unrar(archive: Path, target: Path) -> None:
    tool = bsdtar()
    if tool is None:
        raise UnpackError(
            "Для RAR нужен bsdtar (libarchive): пакет libarchive-tools или SOURCE_BSDTAR"
        )
    result = subprocess.run(  # noqa: S603 - путь к программе из настроек, аргументы — пути
        [tool, "-x", "-f", str(archive), "-C", str(target)],
        capture_output=True,
        check=False,
        timeout=300,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise UnpackError(f"{archive.name}: bsdtar вернул {result.returncode}: {message}")


def _safe_relative(name: str) -> Path | None:
    """Относительный путь внутри каталога; имена с «..» и абсолютные отбрасываются."""
    parts = [part for part in PurePosixPath(name.replace("\\", "/")).parts if part not in {"", "/"}]
    if not parts or any(part == ".." for part in parts) or ":" in parts[0]:
        return None
    return Path(*parts)
