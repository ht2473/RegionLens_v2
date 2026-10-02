"""
Сборка обложки карточки ссылки ``static/img/og-cover.png`` через Playwright.

Одноразовая: пересобрать при смене знака, названия, девиза или охвата набора.
"""

from __future__ import annotations

import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "static" / "fonts"
LOGO = ROOT / "static" / "img" / "logo.svg"
TARGET = ROOT / "static" / "img" / "og-cover.png"

WIDTH, HEIGHT = 1200, 630

# Цвета светлой ветки tokens.css: страница обложки собирается без таблиц стилей.
PAPER = "#f6f3ee"
INK = "#2a2724"
SECONDARY = "#534d46"
MUTED = "#635b52"
LINE = "#d5c9b6"
NO_DATA = "#e2dace"
NO_DATA_STROKE = "#c9bda9"
HONEY = "#c27b02"
SCALE = ("#e4d292", "#c9c36d", "#99b95f", "#60b054", "#34a160", "#138f67", "#077e63")

TITLE = "RegionLens"
TAGLINE = "Социально-экономические показатели регионов России"
FACTS = "85 регионов · 1\u00a0294 показателя · 2001–2025 · данные Росстата"

# Высоты столбцов мотива; пропуск (None) — штриховкой «нет данных».
BARS = (0.34, 0.46, 0.41, 0.58, None, 0.52, 0.66, 0.61, 0.74, 0.7, 0.86, 1.0)


def _font(name: str) -> str:
    """Начертание данными для @font-face."""
    return "data:font/woff2;base64," + base64.b64encode((FONTS / name).read_bytes()).decode("ascii")


def _bars() -> str:
    """Столбцы мотива: ступени шкалы по величине, последний — медовый."""
    known = sorted(value for value in BARS if value is not None)
    cells = []
    for index, value in enumerate(BARS):
        if value is None:
            cells.append('<span class="bar bar--gap"></span>')
            continue
        step = round(known.index(value) / (len(known) - 1) * (len(SCALE) - 1))
        colour = HONEY if index == len(BARS) - 1 else SCALE[step]
        style = f"height: {value * 100:.0f}%; background: {colour}"
        cells.append(f'<span class="bar" style="{style}"></span>')
    return "".join(cells)


def page() -> str:
    """Разметка обложки."""
    logo = LOGO.read_text(encoding="utf-8")
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
@font-face {{ font-family: Onest; src: url("{_font("onest-latin.woff2")}");
  font-weight: 100 900; unicode-range: U+0000-00FF, U+2013, U+00B7; }}
@font-face {{ font-family: Onest; src: url("{_font("onest-cyrillic.woff2")}");
  font-weight: 100 900; unicode-range: U+0400-04FF; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; width: {WIDTH}px; height: {HEIGHT}px; background: {PAPER};
  font-family: Onest, sans-serif; color: {INK}; overflow: hidden; }}
.cover {{ position: relative; height: 100%; padding: 88px 96px 0; }}
.brand {{ display: flex; align-items: center; gap: 28px; }}
.brand svg {{ width: 112px; height: 112px; flex-shrink: 0; }}
.brand b {{ font-size: 96px; font-weight: 700; letter-spacing: -0.02em; line-height: 1; }}
.tagline {{ margin: 40px 0 0; font-size: 34px; font-weight: 500; line-height: 1.25;
  color: {SECONDARY}; }}
.facts {{ margin: 18px 0 0; font-size: 26px; color: {MUTED}; }}
.motif {{ position: absolute; left: 96px; right: 96px; bottom: 0; height: 150px;
  display: flex; align-items: flex-end; gap: 14px; border-bottom: 3px solid {LINE}; }}
.bar {{ flex: 1; border-radius: 8px 8px 0 0; }}
.bar--gap {{ height: 18%; background-color: {NO_DATA};
  background-image: repeating-linear-gradient(
    45deg, {NO_DATA_STROKE} 0 3px, transparent 3px 11px); }}
</style></head><body><div class="cover">
  <div class="brand">{logo}<b>{TITLE}</b></div>
  <p class="tagline">{TAGLINE}</p>
  <p class="facts">{FACTS}</p>
  <div class="motif">{_bars()}</div>
</div></body></html>"""


def build() -> None:
    """Нарисовать обложку и записать её файлом."""
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        view = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        view.set_content(page())
        view.evaluate("document.fonts.ready")
        view.screenshot(path=str(TARGET))
        browser.close()
    print(f"Обложка записана: {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size} байт)")


if __name__ == "__main__":
    build()
