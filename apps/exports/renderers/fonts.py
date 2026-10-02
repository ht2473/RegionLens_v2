"""
Подбор системного шрифта с кириллицей для PDF.

Встроенные шрифты reportlab кириллицы не содержат; перечень путей — ``PDF_FONT_CANDIDATES``,
явный путь — ``PDF_FONT_PATH``.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont

logger = logging.getLogger(__name__)

# Знак, по наличию которого шрифт признаётся пригодным: заглавная «А» кириллицы.
CYRILLIC_PROBE = 0x0410

# Имена, под которыми шрифты регистрируются в reportlab.
REGULAR_FONT = "RegionLens"
BOLD_FONT = "RegionLens-Bold"

# Запасной шрифт без кириллицы — с предупреждением в журнале.
FALLBACK_FONT = "Helvetica"
FALLBACK_BOLD = "Helvetica-Bold"

# Возможные окончания имени файла полужирного начертания рядом с обычным:
# DejaVuSans.ttf → DejaVuSans-Bold.ttf, arial.ttf → arialbd.ttf.
BOLD_SUFFIXES = ("-Bold", "-bold", "bd", "b")


@lru_cache(maxsize=1)
def register_report_fonts() -> tuple[str, str]:
    """Зарегистрировать шрифты отчёта и вернуть пару «обычный, полужирный»; раз за процесс."""
    path = _find_cyrillic_font()
    if path is None:
        logger.warning(
            "Шрифт с кириллицей не найден; PDF будет собран шрифтом %s. "
            "Задайте PDF_FONT_PATH или установите пакет шрифтов DejaVu.",
            FALLBACK_FONT,
        )
        return FALLBACK_FONT, FALLBACK_BOLD

    pdfmetrics.registerFont(TTFont(REGULAR_FONT, str(path)))
    bold_path = _find_bold_variant(path)
    if bold_path is not None:
        pdfmetrics.registerFont(TTFont(BOLD_FONT, str(bold_path)))
        return REGULAR_FONT, BOLD_FONT

    # Полужирного начертания нет: заголовки выделяются размером и цветом.
    return REGULAR_FONT, REGULAR_FONT


def _find_cyrillic_font() -> Path | None:
    """Найти первый доступный шрифт с поддержкой кириллицы."""
    candidates = [settings.PDF_FONT_PATH, *settings.PDF_FONT_CANDIDATES]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file() and _supports_cyrillic(path):
            return path
    return None


def _find_bold_variant(regular: Path) -> Path | None:
    """Подобрать полужирное начертание рядом с обычным."""
    stem = regular.stem
    for suffix in BOLD_SUFFIXES:
        candidate = regular.with_name(f"{stem}{suffix}{regular.suffix}")
        if candidate.is_file() and _supports_cyrillic(candidate):
            return candidate
    return None


def _supports_cyrillic(path: Path) -> bool:
    """Проверить, что шрифт содержит кириллические знаки."""
    try:
        font = TTFont(path.stem, str(path))
    except TTFError, OSError, ValueError:
        return False
    return CYRILLIC_PROBE in font.face.charToGlyph
