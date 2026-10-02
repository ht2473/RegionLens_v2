"""
Проверки спрайта значков: каждый значок, названный в коде, есть в спрайте, в спрайте нет
неиспользуемых, и у представлений и инструментов значки не повторяются.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

from apps.analytics.tools import TOOLS
from apps.surface.panels import PANELS

pytestmark = pytest.mark.unit

BASE_DIR = Path(settings.BASE_DIR)
SPRITE = BASE_DIR / "templates" / "partials" / "_icons.html"
SOURCE_SUFFIXES = {".html", ".js", ".py"}

# Значки в разметке и сценариях: href="#icon-…" с именем, записанным буквами.
MARKUP_RE = re.compile(r"#icon-([a-z][a-z-]*[a-z])\b")
# Значки, которые шаблон получает из Python: icon="…", "icon": "…" и третье поле видов экрана.
PYTHON_RE = re.compile(r"""(?:\bicon=|["']icon["']:\s*)["']([a-z-]+)["']""")
QUERY_TARGET_RE = re.compile(r'QueryTarget\("[^"]+", "[^"]+", _\("[^"]+"\), "([a-z-]+)"\)')
# Запасной значок, подставляемый шаблоном.
DEFAULT_RE = re.compile(r"icon\|default:'([a-z-]+)'")


def sprite_icons() -> set[str]:
    """Имена значков в спрайте."""
    return set(re.findall(r'<symbol id="icon-([a-z-]+)"', SPRITE.read_text(encoding="utf-8")))


def used_icons() -> set[str]:
    """Имена значков, упомянутых в шаблонах, сценариях и коде на Python."""
    found: set[str] = set()
    for root in ("apps", "templates", "static/js"):
        for path in (BASE_DIR / root).rglob("*"):
            if path.suffix not in SOURCE_SUFFIXES or path == SPRITE or "migrations" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".py":
                found.update(PYTHON_RE.findall(text))
                found.update(QUERY_TARGET_RE.findall(text))
            else:
                found.update(MARKUP_RE.findall(text))
                found.update(DEFAULT_RE.findall(text))
    return found


def test_every_used_icon_is_in_the_sprite() -> None:
    """Значок, которого нет в спрайте, браузер рисует пустым местом."""
    missing = used_icons() - sprite_icons()
    assert not missing, f"нет в спрайте: {sorted(missing)}"


def test_sprite_has_no_unused_icons() -> None:
    """Неиспользуемые значки удалены из спрайта."""
    unused = sprite_icons() - used_icons()
    assert not unused, f"не используются: {sorted(unused)}"


def test_views_and_tools_have_distinct_icons() -> None:
    """У каждого представления и инструмента свой значок: один значок — один раздел."""
    icons = [panel.icon for panel in PANELS] + [tool.icon for tool in TOOLS]
    assert len(icons) == len(set(icons)), icons
