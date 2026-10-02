"""
Проверки шкалы картограммы и палитры рядов по значениям из tokens.css: монотонность светлоты,
отделённость нижней ступени от подложки, различимость границы, контраст палитры в обеих темах.
"""

from __future__ import annotations

import math
import re
from itertools import combinations, pairwise
from pathlib import Path

import pytest
from django.conf import settings

pytestmark = pytest.mark.unit

# Наименьший допустимый контраст нижней ступени шкалы к подложке карты.
BACKDROP_CONTRAST = 1.2

# Наименьший допустимый контраст границы субъекта к разделяемым ею заливкам.
BORDER_CONTRAST = 1.3

# Наименьший допустимый контраст соседних ступеней шкалы.
STEP_CONTRAST = 1.2

# Наименьший допустимый контраст линии графика к поверхности карточки.
SERIES_CONTRAST = 3.0

# Наименьшее различие двух цветов палитры рядов в единицах CIE Lab.
SERIES_DIFFERENCE = 20.0

# Наименьший допустимый контраст подписи у конца линии к поверхности, на которой
# она стоит: подпись — мелкий текст, и требование к ней строже, чем к самой линии.
INK_CONTRAST = 4.5

# Наибольшее допустимое различие подписи и её линии в единицах CIE Lab: подпись
# должна читаться как продолжение линии, а не как другой цвет.
INK_DIFFERENCE = 20.0

SEQUENTIAL = tuple(f"--scale-seq-{step}" for step in range(1, 8))
SERIES = tuple(f"--series-{index}" for index in range(1, 9))
INKS = (*(f"{name}-ink" for name in SERIES), "--accent-ink")
DIVERGING = (
    "--scale-div-neg-3",
    "--scale-div-neg-2",
    "--scale-div-neg-1",
    "--scale-div-zero",
    "--scale-div-pos-1",
    "--scale-div-pos-2",
    "--scale-div-pos-3",
)


# ---------------------------------------------------------------------------------------
# Чтение файла оформления
# ---------------------------------------------------------------------------------------


def blocks() -> tuple[dict[str, str], dict[str, str]]:
    """Прочитать значения переменных светлой и тёмной темы из записей light-dark()."""
    text = (Path(settings.BASE_DIR) / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
    declarations = _declarations(text, r":root \{")
    light = {name: _branch(value, 0) for name, value in declarations.items()}
    dark = {name: _branch(value, 1) for name, value in declarations.items()}
    return light, dark


def _branch(value: str, index: int) -> str:
    """Выбрать светлую или тёмную ветвь записи ``light-dark(светлый, тёмный)``."""
    value = value.strip()
    if not value.startswith("light-dark("):
        return value
    inner = value[len("light-dark(") : value.rindex(")")]
    parts: list[str] = []
    depth = 0
    current = ""
    for char in inner:
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        current += char
    parts.append(current)
    assert len(parts) == 2, f"Ожидались две ветви light-dark(), дано {value}"
    return parts[index].strip()


def _declarations(text: str, opening: str) -> dict[str, str]:
    """Разобрать объявления переменных внутри блока правил."""
    match = re.search(opening, text)
    assert match, f"Блок {opening} не найден в tokens.css"
    body = text[match.end() : text.index("\n}", match.end())]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


def colour(name: str, values: dict[str, str]) -> tuple[int, int, int]:
    """Получить цвет переменной, раскрыв ссылки на другие переменные."""
    value = values[name.strip()].strip()
    while value.startswith("var("):
        value = values[value[4:-1].strip()].strip()
    assert value.startswith("#"), f"{name}: ожидался цвет шестнадцатеричной записью, дано {value}"
    digits = value.lstrip("#")
    return tuple(int(digits[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def translucent(name: str, values: dict[str, str]) -> tuple[tuple[int, int, int], float]:
    """Разобрать полупрозрачный цвет вида ``rgb(r g b / доля)``."""
    match = re.match(r"rgb\((\d+) (\d+) (\d+) / ([\d.]+)\)", values[name].strip())
    assert match, f"{name}: ожидалась запись rgb(r g b / доля)"
    red, green, blue, alpha = match.groups()
    return (int(red), int(green), int(blue)), float(alpha)


# ---------------------------------------------------------------------------------------
# Цветовые расчёты
# ---------------------------------------------------------------------------------------


def lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """Перевести цвет в пространство CIE Lab, где расстояние отвечает различимости."""

    def channel(value: float) -> float:
        share = value / 255
        return share / 12.92 if share <= 0.04045 else ((share + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    white = (0.95047, 1.0, 1.08883)
    coordinates = (
        (0.4124 * red + 0.3576 * green + 0.1805 * blue) / white[0],
        (0.2126 * red + 0.7152 * green + 0.0722 * blue) / white[1],
        (0.0193 * red + 0.1192 * green + 0.9505 * blue) / white[2],
    )

    def curve(value: float) -> float:
        return value ** (1 / 3) if value > (6 / 29) ** 3 else value / (3 * (6 / 29) ** 2) + 4 / 29

    x, y, z = (curve(value) for value in coordinates)
    return 116 * y - 16, 500 * (x - y), 200 * (y - z)


def difference(one: tuple[int, int, int], two: tuple[int, int, int]) -> float:
    """Определить расстояние между цветами в CIE Lab."""
    first, second = lab(one), lab(two)
    return math.dist(first, second)


def luminance(rgb: tuple[float, float, float]) -> float:
    """Вычислить относительную яркость цвета по определению WCAG."""

    def channel(value: float) -> float:
        share = value / 255
        return share / 12.92 if share <= 0.03928 else ((share + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])


def contrast(one: tuple[float, float, float], two: tuple[float, float, float]) -> float:
    """Вычислить отношение контраста двух цветов."""
    first, second = sorted((luminance(one), luminance(two)), reverse=True)
    return (first + 0.05) / (second + 0.05)


def over(
    line: tuple[int, int, int], alpha: float, fill: tuple[float, float, float]
) -> tuple[float, float, float]:
    """Наложить полупрозрачный цвет на заливку."""
    return tuple(alpha * line[index] + (1 - alpha) * fill[index] for index in range(3))  # type: ignore[return-value]


@pytest.fixture(scope="module")
def themes() -> tuple[dict[str, str], dict[str, str]]:
    """Переменные оформления светлой и тёмной темы."""
    return blocks()


THEMES = ("светлая", "тёмная")


def variables(themes: tuple[dict[str, str], dict[str, str]], theme: str) -> dict[str, str]:
    """Выбрать набор переменных по названию темы."""
    return themes[THEMES.index(theme)]


class TestSequentialScale:
    """Последовательная шкала."""

    @pytest.mark.parametrize("theme", THEMES)
    def test_lightness_is_monotone(self, themes: tuple[dict[str, str], ...], theme: str) -> None:
        """Светлота ступеней меняется в одну сторону: порядок читается и без различения цвета."""
        values = variables(themes, theme)
        steps = [luminance(colour(name, values)) for name in SEQUENTIAL]
        assert steps == sorted(steps) or steps == sorted(steps, reverse=True)

    @pytest.mark.parametrize("theme", THEMES)
    def test_neighbouring_steps_are_distinguishable(
        self, themes: tuple[dict[str, str], ...], theme: str
    ) -> None:
        """Соседние ступени отличаются достаточно, чтобы их не путать."""
        values = variables(themes, theme)
        for first, second in pairwise(SEQUENTIAL):
            ratio = contrast(colour(first, values), colour(second, values))
            assert ratio >= STEP_CONTRAST, f"{theme}: {first} и {second} — {ratio:.2f}"


class TestBackdrop:
    """Подложка карты."""

    @pytest.mark.parametrize("theme", THEMES)
    def test_lowest_step_differs_from_the_backdrop(
        self, themes: tuple[dict[str, str], ...], theme: str
    ) -> None:
        """
        Нижняя ступень шкалы отличается от подложки карты.

        Иначе субъект с наименьшим значением сливается с местом, где карты нет.
        """
        values = variables(themes, theme)
        ratio = contrast(colour("--scale-seq-1", values), colour("--map-backdrop", values))
        assert ratio >= BACKDROP_CONTRAST, f"{theme}: {ratio:.2f}"

    @pytest.mark.parametrize("theme", THEMES)
    def test_zero_of_the_diverging_scale_differs_from_the_backdrop(
        self, themes: tuple[dict[str, str], ...], theme: str
    ) -> None:
        """
        Нулевая ступень расходящейся шкалы отличается от подложки.

        В режиме сравнения лет нулём обозначено «изменения нет» — это значение,
        а не отсутствие карты.
        """
        values = variables(themes, theme)
        ratio = contrast(colour("--scale-div-zero", values), colour("--map-backdrop", values))
        assert ratio >= 1.1, f"{theme}: {ratio:.2f}"

    @pytest.mark.parametrize("theme", THEMES)
    def test_missing_data_differs_from_the_backdrop(
        self, themes: tuple[dict[str, str], ...], theme: str
    ) -> None:
        """Штриховка «нет данных» отличается от подложки: это разные состояния."""
        values = variables(themes, theme)
        ratio = contrast(colour("--no-data-stroke", values), colour("--map-backdrop", values))
        assert ratio >= 1.1, f"{theme}: {ratio:.2f}"


class TestBorder:
    """Граница субъекта на карте."""

    @pytest.mark.parametrize("theme", THEMES)
    @pytest.mark.parametrize("scale", (SEQUENTIAL, DIVERGING))
    def test_border_separates_neighbouring_classes(
        self, themes: tuple[dict[str, str], ...], theme: str, scale: tuple[str, ...]
    ) -> None:
        """
        Граница различима от обеих заливок, которые она разделяет.

        Обводку рисуют оба субъекта, поэтому видна та из линий, что сильнее отличается.
        """
        values = variables(themes, theme)
        line, alpha = translucent("--map-border", values)
        for first, second in pairwise(scale):
            one = colour(first, values)
            two = colour(second, values)
            drawn = max(
                (over(line, alpha, one), over(line, alpha, two)),
                key=lambda painted: min(contrast(painted, one), contrast(painted, two)),
            )
            ratio = min(contrast(drawn, one), contrast(drawn, two))
            assert ratio >= BORDER_CONTRAST, f"{theme}: {first} и {second} — {ratio:.2f}"

    @pytest.mark.parametrize("theme", THEMES)
    def test_border_is_translucent(self, themes: tuple[dict[str, str], ...], theme: str) -> None:
        """
        Граница полупрозрачна.

        Сплошная линия по контуру каждого из восьмидесяти пяти субъектов превращает
        карту в сетку и спорит с раскраской, ради которой карта построена.
        """
        values = variables(themes, theme)
        _line, alpha = translucent("--map-border", values)
        assert 0.2 <= alpha <= 0.7


class TestSeriesPalette:
    """Категориальная палитра рядов на графиках."""

    @pytest.mark.parametrize("theme", THEMES)
    def test_every_colour_stands_out_from_the_card(
        self, themes: tuple[dict[str, str], ...], theme: str
    ) -> None:
        """Каждый цвет ряда даёт не меньше 3 : 1 к поверхности, где нарисован график."""
        values = variables(themes, theme)
        surface = colour("--surface-raised", values)
        for name in SERIES:
            ratio = contrast(colour(name, values), surface)
            assert ratio >= SERIES_CONTRAST, f"{theme}: {name} — {ratio:.2f}"

    @pytest.mark.parametrize("theme", THEMES)
    def test_colours_are_distinguishable_from_each_other(
        self, themes: tuple[dict[str, str], ...], theme: str
    ) -> None:
        """Цвета рядов различимы между собой: иначе две линии читаются как одна."""
        values = variables(themes, theme)
        for first, second in combinations(SERIES, 2):
            gap = difference(colour(first, values), colour(second, values))
            assert gap >= SERIES_DIFFERENCE, f"{theme}: {first} и {second} — {gap:.1f}"

    @pytest.mark.parametrize("theme", THEMES)
    def test_label_ink_is_readable(self, themes: tuple[dict[str, str], ...], theme: str) -> None:
        """Текстовая ступень цвета даёт подписи у конца линии 4,5 : 1 на карточке и бумаге."""
        values = variables(themes, theme)
        for name in INKS:
            for surface in ("--surface-raised", "--surface-base"):
                ratio = contrast(colour(name, values), colour(surface, values))
                assert ratio >= INK_CONTRAST, f"{theme}: {name} на {surface} — {ratio:.2f}"

    @pytest.mark.parametrize("theme", THEMES)
    def test_label_ink_keeps_the_colour_of_its_line(
        self, themes: tuple[dict[str, str], ...], theme: str
    ) -> None:
        """Текстовая ступень — тот же цвет, что у линии, а не соседний из палитры."""
        values = variables(themes, theme)
        for name in (*SERIES, "--accent"):
            gap = difference(colour(name, values), colour(f"{name}-ink", values))
            assert gap <= INK_DIFFERENCE, f"{theme}: {name} — {gap:.1f}"

    def test_themes_use_different_palettes(
        self, themes: tuple[dict[str, str], dict[str, str]]
    ) -> None:
        """
        У тёмной темы своя палитра рядов.

        Проверка закрепляет само решение: единая палитра для обеих тем уже
        приводила к нечитаемым линиям на тёмном фоне.
        """
        light, dark = themes
        assert [light[name] for name in SERIES] != [dark[name] for name in SERIES]
