"""
Числа и пропуски в ячейках таблиц пользователей.

Вид чисел решается по столбцу целиком: десятичная запятая или точка, разряды пробелом,
неразрывным и узким неразрывным пробелом, «%», минус Юникода, номер сноски в конце
(«12,5¹», «12,5 1)», «…1)»). Коды ЕБТ −99999999 и −77777777 — пропуски, только если они
встречаются в столбце.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

NBSP = chr(0xA0)
NARROW_NBSP = chr(0x202F)
THIN_SPACE = chr(0x2009)
MINUS = chr(0x2212)

VALUE = "value"
# Значения нет: «…», «-», «н/д» и т. п.
MISSING = "missing"
# Значение есть у источника, но не публикуется (конфиденциальность, код ЕБТ −77777777).
HIDDEN = "hidden"
# В ячейке текст, а не число: «в 2,0 р.».
TEXT = "text"
EMPTY = "empty"

# Знаки пропуска: многоточие, прочерки, крест, «нет данных».
MISSING_MARKS = frozenset(
    {
        "…",
        "...",
        "..",
        "-",
        "–",
        "—",
        MINUS,
        "x",
        "х",
        "X",
        "Х",
        "*",
        "н/д",
        "нд",
        "н.д.",
        "нет данных",
        "нет",
        "не имеется",
        "n/a",
        "na",
        "nan",
        "null",
        "none",
        "#н/д",
        "#n/a",
        "#div/0!",
        "#дел/0!",
        "#value!",
        "#знач!",
    }
)
EBT_MISSING = -99999999.0
EBT_HIDDEN = -77777777.0

_SUPERSCRIPTS = "¹²³⁴⁵⁶⁷⁸⁹⁰"
_SPACES = re.compile(f"[ {NBSP}{NARROW_NBSP}{THIN_SPACE}]")
# Номер сноски в конце: «1)», «2);3)», «¹», «*».
_NOTE_TAIL = re.compile(rf"(?:\s*\d{{1,2}}\)\s*;?)+$|[{_SUPERSCRIPTS}*]+$")
_NOTE_NUMBERS = re.compile(r"(\d{1,2})\)")
_PLAIN = re.compile(r"^[+-]?(\d+)(?:[.,](\d+))?$")
_GROUPED_SPACE = re.compile(r"^[+-]?\d{1,3}(?: \d{3})+(?:[.,]\d+)?$")
_GROUPED_COMMA = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_GROUPED_DOT = re.compile(r"^[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?$")
_SIMPLE = re.compile(r"-?\d+(?:\.\d+)?\Z")
_EBT_TEXTS = frozenset({"-99999999", "-77777777"})
# «….», «…..»: многоточие с лишними точками и сдвоенные прочерки.
_MARK_WITH_NOTE = re.compile(r"(…|\.{2,3}|[-–—хx])\s*(\d{1,2})\Z")
_ONLY_DOTS = re.compile(r"[.…\-–—]{2,}\Z")
_SCIENTIFIC = re.compile(r"^[+-]?\d+(?:[.,]\d+)?[eE][+-]?\d+$")


@dataclass(frozen=True, slots=True)
class Cell:
    """Разобранная ячейка: число или вид пропуска и номера сносок."""

    status: str
    value: float | None = None
    notes: tuple[int, ...] = ()
    percent: bool = False


@dataclass(frozen=True, slots=True)
class NumberStyle:
    """Вид чисел столбца: десятичный знак, знак разрядов и обычное число знаков после запятой."""

    decimal: str = ","
    grouping: str = " "
    decimals: int | None = None
    ebt_codes: bool = False


def text_of(raw: Any) -> str:
    """Ячейка как текст без краевых пробелов; число Excel — как есть."""
    if raw is None:
        return ""
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw).strip()


def guess_style(  # noqa: PLR0912 — по ветви на вид записи числа
    cells: Iterable[Any], *, delimiter: str = ";"
) -> NumberStyle:
    """Вид чисел по столбцу: где десятичная запятая, а где разряды."""
    comma = dot = 0
    decimals: Counter[int] = Counter()
    ebt = False
    for raw in cells:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int | float):
            ebt = ebt or raw in (EBT_MISSING, EBT_HIDDEN)
            continue
        text = str(raw).strip().replace(MINUS, "-")
        if not _SIMPLE.match(text):
            text = _strip_note(_SPACES.sub("", text))[0].rstrip("%")
        if text in _EBT_TEXTS:
            ebt = True
            continue
        if _GROUPED_COMMA.match(text) and "." in text:
            dot += 2
        elif _GROUPED_DOT.match(text) and "," in text:
            comma += 2
        match = _PLAIN.match(text)
        if match and match.group(2) is not None:
            places = len(match.group(2))
            separator = "," if "," in text else "."
            # «1,234» неоднозначно: разряды или три знака после запятой.
            if places != 3:  # noqa: PLR2004
                if separator == ",":
                    comma += 1
                else:
                    dot += 1
            decimals[places] += 1
    if comma > dot:
        decimal = ","
    elif dot > comma:
        decimal = "."
    else:
        decimal = "." if delimiter == "," else ","
    common = decimals.most_common(1)[0][0] if decimals else None
    return NumberStyle(
        decimal=decimal,
        grouping="," if decimal == "." else " ",
        decimals=common,
        ebt_codes=ebt,
    )


def parse(raw: Any, style: NumberStyle | None = None) -> Cell:  # noqa: PLR0911
    """Разобрать ячейку по виду чисел столбца."""
    style = style or NumberStyle()
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return Cell(EMPTY)
    if isinstance(raw, bool):
        return Cell(TEXT)
    if isinstance(raw, int | float):
        number = float(raw)
        if math.isnan(number):
            return Cell(MISSING)
        if style.ebt_codes and number == EBT_MISSING:
            return Cell(MISSING)
        if style.ebt_codes and number == EBT_HIDDEN:
            return Cell(HIDDEN)
        return Cell(VALUE, number)
    text = str(raw).strip().replace(MINUS, "-")
    # Число без разрядов и сносок — так пишут выгрузки и числа Excel, прочитанные текстом.
    if _SIMPLE.match(text) and not (style.ebt_codes and text in _EBT_TEXTS):
        return Cell(VALUE, float(text))
    body, notes = _strip_note(text)
    compact = _SPACES.sub("", body)
    marker = " ".join(body.lower().split())
    if (
        marker in MISSING_MARKS
        or compact.lower() in MISSING_MARKS
        or _ONLY_DOTS.match(compact)
        or (not compact and notes)
    ):
        return Cell(MISSING, notes=notes)
    percent = compact.endswith("%")
    if percent:
        compact = compact[:-1]
    value = _number(compact, body, style)
    if value is None and notes and _NOTE_TAIL.search(text) and text.endswith(")"):
        # «106,52)»: две цифры после запятой там, где в столбце одна, — номер сноски.
        value, notes = _glued_note(text, style)
    if value is None:
        return Cell(TEXT, notes=notes)
    if style.ebt_codes and value in (EBT_MISSING, EBT_HIDDEN):
        return Cell(MISSING if value == EBT_MISSING else HIDDEN)
    return Cell(VALUE, value, notes, percent)


def parse_column(cells: Sequence[Any], *, delimiter: str = ";") -> tuple[NumberStyle, list[Cell]]:
    """Вид чисел столбца и его разобранные ячейки."""
    style = guess_style(cells, delimiter=delimiter)
    return style, [parse(cell, style) for cell in cells]


def numeric_share(cells: Sequence[Cell]) -> float:
    """Доля чисел и пропусков среди непустых ячеек."""
    filled = [cell for cell in cells if cell.status != EMPTY]
    if not filled:
        return 0.0
    return sum(cell.status in {VALUE, MISSING, HIDDEN} for cell in filled) / len(filled)


def _strip_note(text: str) -> tuple[str, tuple[int, ...]]:
    """Отделить номер сноски в конце: «12,5 1)» → («12,5», (1,)), «…2» → («…», (2,))."""
    marked = _MARK_WITH_NOTE.match(text)
    if marked:
        return marked.group(1), (int(marked.group(2)),)
    match = _NOTE_TAIL.search(text)
    if match is None or (match.start() == 0 and not text.startswith(("…", ".", "-", "–", "—"))):
        return text, ()
    tail = match.group()
    numbers = tuple(int(number) for number in _NOTE_NUMBERS.findall(tail))
    numbers += tuple(
        _SUPERSCRIPTS.index(char) + 1 for char in tail if char in _SUPERSCRIPTS and char != "⁰"
    )
    return text[: match.start()].strip(), numbers


def _number(compact: str, body: str, style: NumberStyle) -> float | None:  # noqa: PLR0911
    """Число из текста без пробелов; ``None`` — не число."""
    if not compact or not any(char.isdigit() for char in compact):
        return None
    if _SCIENTIFIC.match(compact):
        return float(compact.replace(",", "."))
    spaced = _SPACES.sub(" ", body).strip()
    if _GROUPED_SPACE.match(spaced):
        return float(spaced.replace(" ", "").replace(",", "."))
    if style.decimal == ",":
        if _GROUPED_DOT.match(compact) and "," in compact:
            return float(compact.replace(".", "").replace(",", "."))
        if re.fullmatch(r"[+-]?\d+(,\d+)?", compact):
            return float(compact.replace(",", "."))
        if re.fullmatch(r"[+-]?\d+\.\d+", compact):
            return float(compact)
        return None
    if _GROUPED_COMMA.match(compact):
        return float(compact.replace(",", ""))
    if re.fullmatch(r"[+-]?\d+(\.\d+)?", compact):
        return float(compact)
    if re.fullmatch(r"[+-]?\d+,\d+", compact):
        return float(compact.replace(",", "."))
    return None


def _glued_note(text: str, style: NumberStyle) -> tuple[float | None, tuple[int, ...]]:
    """«106,52)» при одном знаке после запятой в столбце — 106,5 и сноска 2."""
    match = re.fullmatch(r"([+-]?\d[\d\s]*[.,](\d+))(\d)\)\s*", _SPACES.sub(" ", text))
    if match is None or style.decimals is None or len(match.group(2)) != style.decimals:
        return None, ()
    value = _number(_SPACES.sub("", match.group(1)), match.group(1), style)
    return value, (int(match.group(3)),)
