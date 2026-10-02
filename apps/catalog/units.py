"""
Компактная подпись единицы измерения из свободной строки источника.

Порядок правил: отношение к основанию, основная единица, множитель, строки без единицы.
"""

from __future__ import annotations

import re

# Предельная длина подписи единицы измерения на осях графиков и в таблицах.
MAX_SHORT_UNIT_LENGTH = 24

# Объявления без единицы измерения: момент наблюдения, охват, источник сведений.
# Проверяются последними: «По состоянию на конец отчётного периода, тысяч» несёт множитель.
_NO_UNIT_PATTERNS: tuple[str, ...] = (
    r"^не указана$",
    r"^на\s+\d{1,2}\s+[а-яё]+$",
    r"^по\s+состоянию\s+на\b",
    r"^по\s+(данным|итогам|утвержденным)\b",
    r"^на\s+(конец|начало)\b",
    r"^в\s+среднем\s+за\s+год$",
    r"^оценка\s+на\b",
    r"^в\s+(фактически\s+действовавших|текущих|постоянных|сопоставимых)\s+ценах$",
)

# Названия месяцев: «на 1 января» — момент наблюдения, а не отношение к основанию.
_MONTHS = (
    "январ",
    "феврал",
    "март",
    "апрел",
    "ма[йя]",
    "июн",
    "июл",
    "август",
    "сентябр",
    "октябр",
    "ноябр",
    "декабр",
)

# Множители. Порядок значим: «миллиард» иначе совпал бы с образцом «миллион».
_MULTIPLIERS: tuple[tuple[str, str], ...] = (
    (r"триллион\w*|\bтрлн\b", "трлн"),
    (r"миллиард\w*|\bмлрд\b", "млрд"),
    (r"миллион\w*|\bмлн\b", "млн"),
    (r"тысяч\w*|\bтыс\b", "тыс."),
)

# Основные единицы; составные раньше простых, чтобы «квадратных метров» не стало «метрами».
_BASE_UNITS: tuple[tuple[str, str], ...] = (
    (r"рубл\w*|\bруб\b", "руб."),
    (r"доллар\w*", "долл."),
    (r"\bевро\b", "евро"),
    (r"промилле", "‰"),
    (r"процентн\w*\s+пункт\w*|\bп\.\s*п\.", "п. п."),
    (r"процент\w*|%", "%"),
    (r"кубическ\w*\s+метр\w*|\bм3\b|\bм³", "м³"),
    (r"квадратн\w*\s+метр\w*|\bм2\b|\bм²", "м²"),
    (r"километр\w*|\bкм\b", "км"),
    (r"гектар\w*|\bга\b", "га"),
    (r"центнер\w*", "ц"),
    (r"килограмм\w*|\bкг\b", "кг"),
    (r"\bтонн\w*|\bт\b", "т"),
    (r"лошадин\w*\s+сил\w*|\bл\.\s*с\b", "л. с."),
    (r"литр\w*|\bл\b", "л"),
    (r"киловатт-час\w*|\bквт·ч\b", "кВт·ч"),
    (r"гигакалори\w*|\bгкал\b", "Гкал"),
    (r"экземпляр\w*|\bэкз\b", "экз."),
    (r"голов\w*", "голов"),
    (r"посещен\w*", "посещений"),
    (r"челов\w*|\bчел\b|жител\w*|пациент\w*", "чел."),
    (r"\bкоек\b|\bкойк\w*", "коек"),
    # Только «мест»: «места» встречается в обороте «исходя из места привлечения средств».
    (r"\bмест\b", "мест"),
    (r"штук\w*|\bшт\b", "шт."),
    (r"случа\w*|заболеван\w*", "случаев"),
    (r"единиц\w*|\bед\b", "ед."),
    (r"\bлет\b|\bгод\w*\s+жизни\b", "лет"),
)

# Обозначения для объявления целиком: смысл несёт всё выражение.
_WHOLE_STRING: dict[str, str] = {
    "в процентах к предыдущему году": "% к пред. году",
    "в постоянных ценах, в процентах к предыдущему году": "% к пред. году",
    "в процентах к предыдущему году, в сопоставимых ценах": "% к пред. году",
    "в процентах к итогу": "% к итогу",
}

# Отношение к основанию: «на 1000 человек населения»; основание сокращается как единица.
_RATIO_PATTERN = re.compile(
    r"на\s+(?P<count>\d[\d\s]*)\s+(?P<base>[а-яё]+(?:\s+[а-яё]+)?)",
    re.IGNORECASE,
)

# Единицы, для которых множитель не имеет смысла: доля и разность долей безразмерны.
_DIMENSIONLESS = {"%", "‰", "п. п."}

# Пороги сокращения основания: «на 100 000» — «на 100 тыс.», «на 1000» остаётся числом.
_MILLION = 1_000_000
_THOUSAND = 1000
_MIN_THOUSANDS = 10_000

_NO_UNIT = tuple(re.compile(p, re.IGNORECASE) for p in _NO_UNIT_PATTERNS)
_MONTH_PATTERN = re.compile("|".join(_MONTHS), re.IGNORECASE)
_MULTIPLIER_RULES = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _MULTIPLIERS)
_BASE_RULES = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _BASE_UNITS)


def short_unit_label(name: str) -> str:
    """
    Построить компактную подпись единицы: «млн руб.», «на 1000 чел.», «тыс. м³».

    Пустая строка — единицы в объявлении нет; нераспознанное усекается по границе слова.
    """
    cleaned = " ".join(str(name).split())
    if not cleaned:
        return ""

    lowered = cleaned.lower()
    if lowered in _WHOLE_STRING:
        return _WHOLE_STRING[lowered]

    base, position = _base_unit(lowered)
    ratio, ratio_position = _ratio_label(lowered)

    # Отношение — только если единица не названа раньше него: в «Кг условного топлива
    # на 10 тысяч рублей» измеряют килограммы.
    if ratio and (not base or ratio_position < position):
        return ratio

    if not base:
        return _without_base(lowered, cleaned)

    if base in _DIMENSIONLESS:
        return base

    # Множитель — только до единицы: в «на 10 тысяч рублей» тысяча относится к основанию.
    prefix = _multiplier(lowered, before=position)
    return f"{prefix} {base}".strip() if prefix else base


def _without_base(lowered: str, cleaned: str) -> str:
    """Подобрать подпись без единицы: множитель («тыс.») либо пустую строку."""
    prefix = _multiplier(lowered)
    if prefix:
        return prefix
    if any(pattern.search(lowered) for pattern in _NO_UNIT):
        return ""
    return _truncate_at_word(cleaned)


def _base_unit(lowered: str) -> tuple[str, int]:
    """Найти единицу, признак которой встречается в строке раньше прочих."""
    best: tuple[str, int] | None = None
    for pattern, label in _BASE_RULES:
        match = pattern.search(lowered)
        if match is None:
            continue
        if best is None or match.start() < best[1]:
            best = (label, match.start())
    return best if best else ("", -1)


def _multiplier(lowered: str, *, before: int | None = None) -> str:
    """Найти множитель, названный до указанного места строки."""
    window = lowered if before is None else lowered[:before]
    for pattern, label in _MULTIPLIER_RULES:
        if pattern.search(window):
            return label
    return ""


def _ratio_label(lowered: str) -> tuple[str, int]:
    """Построить подпись отношения к основанию и вернуть её место; «на 1 января» — не отношение."""
    for match in _RATIO_PATTERN.finditer(lowered):
        base_text = match.group("base").strip()
        if _MONTH_PATTERN.match(base_text):
            continue

        count = " ".join(match.group("count").split())
        base, _ = _base_unit(base_text)
        if not base:
            # Основание, не сводимое к единице («на 1000 браков»), сохраняется как есть.
            base = base_text.split()[0]

        return f"на {_normalize_count(count)} {base}".strip(), match.start()

    return "", -1


def _normalize_count(count: str) -> str:
    """Привести числовое основание отношения к краткому виду."""
    digits = count.replace(" ", "")
    if not digits.isdigit():
        return count
    number = int(digits)
    if number >= _MILLION and number % _MILLION == 0:
        return f"{number // _MILLION} млн"
    if number >= _MIN_THOUSANDS and number % _THOUSAND == 0:
        return f"{number // _THOUSAND} тыс."
    return str(number)


def _truncate_at_word(value: str) -> str:
    """Усечь строку по границе слова."""
    if len(value) <= MAX_SHORT_UNIT_LENGTH:
        return value

    head = value[: MAX_SHORT_UNIT_LENGTH - 1]
    space = head.rfind(" ")
    if space > MAX_SHORT_UNIT_LENGTH // 2:
        head = head[:space]
    return f"{head.rstrip(' ,;')}…"
