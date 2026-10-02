"""
Проверки начертаний: узкое покрывает русский алфавит и уже основного, оценка ширины
знака на карте следует за ним, в каталоге шрифтов нет лишнего.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

pytestmark = pytest.mark.unit

# Наименьшее сужение, ради которого узкое начертание вообще имеет смысл добавлять.
MIN_NARROWING = 0.10

# Русский алфавит в обоих регистрах: то, чем набрано всё в этой системе.
RUSSIAN = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдежзийклмнопрстуфхцчшщъыьэюя"

# Настоящие названия субъектов с пробелами и дефисами — как в подписях карты.
LABELS = (
    "Ханты-Мансийский автономный округ",
    "Ямало-Ненецкий автономный округ",
    "Ненецкий автономный округ",
    "Чукотский автономный округ",
    "Сахалинская область",
    "Республика Ингушетия",
)


@pytest.fixture(scope="module")
def static_dir() -> Path:
    """Каталог исходной статики."""
    return Path(settings.BASE_DIR) / "static"


@pytest.fixture(scope="module")
def css(static_dir: Path) -> str:
    """Все таблицы стилей одной строкой."""
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((static_dir / "css").glob("*.css"))
    )


def average_glyph_width(paths: tuple[Path, ...], text: str) -> float:
    """
    Средняя ширина знака в долях кегля.

    Поднаборы перечисляются вместе: кириллический и латинский лежат отдельными
    файлами, а в подписи встречаются и буквы, и пробелы с дефисами.
    """
    fonttools = pytest.importorskip("fontTools.ttLib", reason="fontTools не установлен")

    widths: list[float] = []
    for path in paths:
        font = fonttools.TTFont(path)
        cmap = font.getBestCmap()
        metrics = font["hmtx"].metrics
        upm = font["head"].unitsPerEm
        widths.extend(metrics[cmap[ord(char)]][0] / upm for char in text if ord(char) in cmap)

    assert widths, "в начертании нет ни одного знака из образца"
    return sum(widths) / len(widths)


class TestFontFiles:
    """Состав каталога начертаний."""

    def test_every_declaration_has_a_file(self, static_dir: Path, css: str) -> None:
        """У каждого объявления начертания есть файл."""
        missing = [
            name
            for name in re.findall(r'url\("\.\./fonts/([^"]+)"\)', css)
            if not (static_dir / "fonts" / name).exists()
        ]
        assert not missing, "объявлены, но отсутствуют: " + ", ".join(missing)

    def test_every_file_is_declared(self, static_dir: Path, css: str) -> None:
        """
        Каждый файл начертания объявлен.

        Файл, оставшийся от убранного начертания, ничем себя не выдаёт: он просто
        лежит в репозитории и попадает в сборку статики.
        """
        declared = set(re.findall(r'url\("\.\./fonts/([^"]+)"\)', css))
        present = {path.name for path in (static_dir / "fonts").iterdir() if path.is_file()}
        assert not present - declared, "лежат, но никем не объявлены: " + ", ".join(
            sorted(present - declared)
        )


class TestNarrowFace:
    """Узкое начертание для плотных мест."""

    def test_token_is_declared(self, static_dir: Path) -> None:
        """Узкое начертание объявлено переменной оформления."""
        tokens = (static_dir / "css" / "tokens.css").read_text(encoding="utf-8")
        assert "--font-narrow:" in tokens

    def test_dense_places_use_it(self, css: str) -> None:
        """Узким начертанием набраны таблица данных, подписи картограммы и легенда шкалы."""
        for selector in (".data-table", ".geo-map__label", ".tile-map__label", ".map-legend"):
            block = re.search(re.escape(selector) + r"\s*\{[^}]*\}", css)
            assert block, f"правило {selector} не найдено"
            assert "var(--font-narrow)" in block.group(0), f"{selector} набран не узким"

    def test_it_covers_the_russian_alphabet(self, static_dir: Path) -> None:
        """Узкое начертание содержит русские буквы: иначе браузер молча подставит запасное."""
        fonttools = pytest.importorskip("fontTools.ttLib", reason="fontTools не установлен")

        font = fonttools.TTFont(static_dir / "fonts" / "roboto-condensed-400.woff2")
        cmap = font.getBestCmap()
        missing = [char for char in RUSSIAN if ord(char) not in cmap]
        assert not missing, "нет русских букв: " + "".join(missing)

    def test_it_is_narrower_than_the_main_face(self, static_dir: Path) -> None:
        """Узкое начертание уже основного не менее чем на десятую часть."""
        fonts = static_dir / "fonts"
        main = average_glyph_width((fonts / "onest-cyrillic.woff2",), RUSSIAN)
        narrow = average_glyph_width((fonts / "roboto-condensed-400.woff2",), RUSSIAN)

        assert 1 - narrow / main >= MIN_NARROWING, (
            f"сужение всего {(1 - narrow / main) * 100:.1f} %: "
            f"основное {main:.3f} em, узкое {narrow:.3f} em"
        )


class TestMapLabelEstimate:
    """Оценка ширины подписи в раскладке картограммы."""

    def test_estimate_follows_the_face(self, static_dir: Path) -> None:
        """
        Оценка ширины знака не меньше действительной и ненамного больше.

        Меньшая даёт наложение подписей, большая отодвигает их от субъектов.
        """
        from apps.maps.cartogram import GLYPH_WIDTH

        fonts = static_dir / "fonts"
        measured = average_glyph_width(
            (fonts / "roboto-condensed-600.woff2", fonts / "roboto-condensed-latin-600.woff2"),
            " ".join(LABELS),
        )
        assert measured <= GLYPH_WIDTH, (
            f"оценка {GLYPH_WIDTH} ниже действительной ширины {measured:.3f} em"
        )
        assert measured * 1.25 >= GLYPH_WIDTH, (
            f"оценка {GLYPH_WIDTH} завышена против {measured:.3f} em более чем на четверть"
        )
