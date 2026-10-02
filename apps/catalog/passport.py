"""
Паспорт региона словами: положение среди регионов, сравнение со страной, изменение за пять лет.

Оценка — только по основному набору с направленностью, проверенной человеком. Изменение
денежных рядов оценивается в реальном выражении: номинальный рост отражает и инфляцию.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils.translation import get_language, gettext, gettext_lazy

from apps.catalog.constants import BreakKind, ValueFlag
from apps.catalog.models import SeriesBreak
from apps.catalog.provenance import Origin, origin_of_year
from apps.catalog.real_terms import Values, deflators, real_between
from apps.catalog.status import SeriesStatus, series_status
from apps.core.templatetags.formatting import NBSP, ru_number
from apps.warehouse.queries import (
    COUNTRY_CODE,
    FeaturedSeries,
    FeaturedTheme,
    featured_set,
    territory_positions,
)
from apps.warehouse.queries.territory import TREND_YEARS

# Доля регионов, начиная с которой положение считается выраженно сильным или слабым.
STRENGTH_SHARE = 0.15

# Границы «середины списка» по доле регионов, которые территория обходит.
MIDDLE_LOW = 0.4
MIDDLE_HIGH = 0.6

# С какого отношения к стране разница называется «в N раз», а не «на N %».
RATIO_WORDING_FROM = 1.5

# Сколько сильных и слабых сторон называет сводка.
SUMMARY_LIMIT = 3

# Знаков после запятой в числе фиксированных наборов на доход.
BASKET_DIGITS = 1

# Ряды фразы о доходе в фиксированных наборах: доход, зарплата и стоимость набора.
BASKET_INCOME = "Y477110374:00"
BASKET_WAGE = "Y477110378:00"
BASKET_COST = "Y477110395:00"

# С какой кратности она называется целым числом: «в 12 раз», а не «в 12,3 раза».
WHOLE_RATIO_FROM = 10

# Английские порядковые: 11th–13th — исключения из правила последней цифры.
ENGLISH_TEENS = range(11, 14)

# Положение словами: нижняя граница доли обойдённых регионов и две формулировки —
# для показателя с направленностью и без неё. Порядок — от лучшего к худшему.
STANDINGS = (
    (
        1 - STRENGTH_SHARE,
        gettext_lazy("одно из лучших значений в стране"),
        gettext_lazy("одно из самых высоких значений в стране"),
    ),
    (
        MIDDLE_HIGH,
        gettext_lazy("лучше, чем в большинстве регионов"),
        gettext_lazy("выше, чем в большинстве регионов"),
    ),
    (
        MIDDLE_LOW,
        gettext_lazy("в середине списка регионов"),
        gettext_lazy("в середине списка регионов"),
    ),
    (
        STRENGTH_SHARE,
        gettext_lazy("хуже, чем в большинстве регионов"),
        gettext_lazy("ниже, чем в большинстве регионов"),
    ),
    (
        -1.0,
        gettext_lazy("одно из худших значений в стране"),
        gettext_lazy("одно из самых низких значений в стране"),
    ),
)

# Тон вывода — имя модификатора оформления.
TONE_GOOD = "good"
TONE_BAD = "bad"
TONE_NEUTRAL = "neutral"


def ordinal(number: int) -> str:
    """Порядковое числительное цифрами: «8-е» по-русски, «8th» по-английски."""
    if get_language() != "en":
        return f"{number}-е"
    if number % 100 in ENGLISH_TEENS:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _rounded_ratio(ratio: float) -> float:
    """Кратность: одним знаком после запятой, начиная с десяти — целым числом."""
    return float(round(ratio)) if ratio >= WHOLE_RATIO_FROM else round(ratio, 1)


def _ratio_text(ratio: float) -> str:
    """Кратность цифрами; целое — без «,0»."""
    return ru_number(ratio, 0 if ratio.is_integer() else 1)


def _times_form(ratio: float) -> bool:
    """
    Нужна ли форма «раза»: «в 1,8 раза», «в 3 раза», но «в 5 раз».

    Сборщик переводов не поддерживает множественные формы, поэтому строк перевода две.
    """
    if not ratio.is_integer():
        return True
    number = int(ratio)
    return number % 10 in (2, 3, 4) and number % 100 not in (12, 13, 14)


def times_phrase(ratio: float) -> str:
    """Кратность словами: «в 4,6 раза», «в 12 раз»."""
    times = _rounded_ratio(ratio)
    if _times_form(times):
        return gettext("в %(ratio)s раза") % {"ratio": _ratio_text(times)}
    return gettext("в %(ratio)s раз") % {"ratio": _ratio_text(times)}


def _share_text(change: float) -> str:
    """Доля изменения в процентах без знака: «15 %»."""
    return f"{ru_number(abs(change) * 100, 0)}{NBSP}%"


def _point_digits(precision: int) -> int:
    """Знаков после запятой у разности в процентных пунктах — не меньше одного."""
    return max(precision, 1)


def _versus_in_points(value: float, country: float, precision: int) -> str:
    """Сравнение процентов со страной разностью в процентных пунктах."""
    digits = _point_digits(precision)
    difference = round(value - country, digits)
    if difference == 0:
        return gettext("на уровне России")
    points = ru_number(abs(difference), digits)
    if difference > 0:
        return gettext("на %(points)s п. п. выше, чем по России") % {"points": points}
    return gettext("на %(points)s п. п. ниже, чем по России") % {"points": points}


def _versus_by_ratio(ratio: float) -> str:
    """Сравнение положительных величин со страной: долей или кратностью."""
    if ratio >= RATIO_WORDING_FROM or ratio <= 1 / RATIO_WORDING_FROM:
        higher = ratio > 1
        times = _rounded_ratio(ratio if higher else 1 / ratio)
        wording = {
            (True, True): gettext_lazy("в %(ratio)s раза выше, чем по России"),
            (True, False): gettext_lazy("в %(ratio)s раз выше, чем по России"),
            (False, True): gettext_lazy("в %(ratio)s раза ниже, чем по России"),
            (False, False): gettext_lazy("в %(ratio)s раз ниже, чем по России"),
        }[(higher, _times_form(times))]
        return str(wording) % {"ratio": _ratio_text(times)}

    if round(abs(ratio - 1) * 100) == 0:
        return gettext("на уровне России")
    share = _share_text(ratio - 1)
    if ratio > 1:
        return gettext("на %(share)s выше, чем по России") % {"share": share}
    return gettext("на %(share)s ниже, чем по России") % {"share": share}


@dataclass(slots=True, frozen=True)
class Change:
    """Изменение величины за период: фраза, направление и сравнимая мера."""

    text: str
    direction: int
    # Мера сравнения со страной: доля, пункты для процентов или разность.
    amount: float
    # Порог, ниже которого разница мер не считается различием.
    tolerance: float


def measure_change(series: FeaturedSeries, value: float, before: float | None) -> Change | None:
    """
    Изменение от ``before`` до ``value`` словами.

    Проценты — в пунктах, величины одного знака — долей, знакопеременные — разностью.
    """
    if before is None:
        return None
    if series.is_percentage:
        digits = _point_digits(series.precision)
        amount = round(value - before, digits)
        tolerance = 10**-digits
        amount_text = gettext("%(points)s п. п.") % {"points": ru_number(abs(amount), digits)}
    elif before > 0 and value > 0:
        amount = round((value - before) / before, 2)
        tolerance = 0.01
        amount_text = _share_text(amount)
    else:
        amount = round(value - before, series.precision)
        tolerance = 10**-series.precision
        amount_text = ru_number(abs(amount), series.precision)

    if amount == 0:
        return Change(gettext("без заметных изменений"), 0, 0.0, tolerance)
    if amount > 0:
        return Change(gettext("рост на %(amount)s") % {"amount": amount_text}, 1, amount, tolerance)
    return Change(
        gettext("снижение на %(amount)s") % {"amount": amount_text}, -1, amount, tolerance
    )


def real_change(ratio: float | None) -> Change | None:
    """Изменение в реальном выражении по отношению конечного значения к начальному."""
    if ratio is None:
        return None
    amount = round(ratio - 1, 2)
    tolerance = 0.01
    if amount == 0:
        return Change(gettext("реально без заметных изменений"), 0, 0.0, tolerance)
    share = _share_text(amount)
    if amount > 0:
        text = gettext("реальный рост на %(amount)s") % {"amount": share}
        return Change(text, 1, amount, tolerance)
    text = gettext("реальное снижение на %(amount)s") % {"amount": share}
    return Change(text, -1, amount, tolerance)


@dataclass(slots=True)
class Position:
    """Положение территории по одному показателю основного набора."""

    series: FeaturedSeries
    year: int
    value: float
    rank_desc: int
    rank_asc: int
    percentile: float
    territories: int
    country_value: float | None = None
    past_year: int | None = None
    past_value: float | None = None
    country_past_value: float | None = None
    # Разрыв внутри пятилетнего окна: изменение через него не считается.
    break_year: int | None = None
    # Внутри окна менялся состав округов или страны: изменение по России не приводится.
    country_break: bool = False
    # Отношение значения к прошлому в неизменных ценах — у денежных рядов с пересчётом.
    real_ratio: float | None = None
    country_real_ratio: float | None = None
    # Признаки значения из внешнего источника (ValueFlag).
    flags: int = 0

    # --- Положение среди регионов ------------------------------------------------------

    @property
    def polarity(self) -> str | None:
        """
        Направленность, по которой оценивается положение, или ничего.

        Абсолютная величина не оценивается: место по ней говорит о размере региона.
        """
        if self.series.absolute or self.series.polarity not in ("positive", "negative"):
            return None
        return self.series.polarity

    @property
    def is_assessed(self) -> bool:
        """Входит ли показатель в оценку сильных и слабых сторон."""
        return self.polarity is not None

    @property
    def favourable(self) -> float | None:
        """Доля регионов, которые территория обходит с учётом направленности, или ничего."""
        if self.polarity == "positive":
            return self.percentile
        if self.polarity == "negative":
            return 1 - self.percentile
        return None

    @property
    def place(self) -> int:
        """Место: первое — лучшее, а у показателей без оценки — наибольшее."""
        return self.rank_asc if self.polarity == "negative" else self.rank_desc

    @property
    def place_text(self) -> str:
        """«8-е место из 85»."""
        return gettext("%(place)s место из %(total)s") % {
            "place": ordinal(self.place),
            "total": self.territories,
        }

    @property
    def scale_position(self) -> str:
        """
        Место на полоске «где регион среди остальных» — доля от левого края, строкой.

        У оцениваемых показателей — от худшего к лучшему, у остальных — по возрастанию.
        """
        share = self.favourable if self.favourable is not None else self.percentile
        return f"{min(1.0, max(0.0, share)):.3f}"

    @property
    def standing(self) -> str:
        """
        Положение среди регионов словами; без оценки — «выше» и «ниже».

        Верхние ступени включают свою границу, как и отбор сильных и слабых сторон.
        """
        favourable = self.favourable
        share = self.percentile if favourable is None else favourable
        for bound, assessed, plain in STANDINGS:
            if share > bound or (share == bound and bound >= MIDDLE_HIGH):
                return str(plain if favourable is None else assessed)
        return ""

    @property
    def tone(self) -> str:
        """Оценка положения для оформления: хорошо, плохо или без оценки."""
        share = self.favourable
        if share is None or MIDDLE_LOW < share < MIDDLE_HIGH:
            return TONE_NEUTRAL
        return TONE_GOOD if share >= MIDDLE_HIGH else TONE_BAD

    # --- Сравнение со страной ----------------------------------------------------------

    @property
    def versus_country(self) -> str:
        """
        Сравнение со значением по России.

        Проценты — разностью в пунктах, остальное — долей или кратностью; при значении
        не больше нуля называется только направление.
        """
        country = self.country_value
        if country is None or self.series.absolute:
            return ""
        if self.series.is_percentage:
            return _versus_in_points(self.value, country, self.series.precision)
        if country <= 0 or self.value <= 0:
            if self.value == country:
                return gettext("на уровне России")
            if self.value > country:
                return gettext("выше, чем по России")
            return gettext("ниже, чем по России")
        return _versus_by_ratio(self.value / country)

    # --- Изменение за пять лет -----------------------------------------------------------

    @property
    def is_money(self) -> bool:
        """Денежный ряд с пересчётом в реальное выражение."""
        return self.series.real is not None

    @property
    def status(self) -> SeriesStatus | None:
        """Состояние публикации ряда у источника; ``None`` — сведений нет."""
        return series_status(self.series.key)

    @property
    def is_preliminary(self) -> bool:
        """Значение предварительное: из оперативного выпуска источника."""
        return bool(self.flags & ValueFlag.PRELIMINARY)

    @property
    def origin(self) -> Origin | None:
        """Откуда значение года, если не из набора."""
        if not self.flags:
            return None
        return origin_of_year(self.series.key, self.year)

    @property
    def nominal_change(self) -> Change | None:
        """Изменение региона за пять лет в единицах ряда; не для темпов и не через разрыв."""
        if self.series.is_growth_index or self.break_year is not None:
            return None
        return measure_change(self.series, self.value, self.past_value)

    @property
    def country_nominal_change(self) -> Change | None:
        """Изменение по стране за те же пять лет в единицах ряда."""
        if (
            self.series.is_growth_index
            or self.series.absolute
            or self.break_year is not None
            or self.country_break
            or self.country_value is None
        ):
            return None
        return measure_change(self.series, self.country_value, self.country_past_value)

    @property
    def change(self) -> Change | None:
        """Изменение, по которому оценивается регион: у денежных рядов — реальное."""
        if not self.is_money:
            return self.nominal_change
        return real_change(self.real_ratio) if self.nominal_change is not None else None

    @property
    def country_change(self) -> Change | None:
        """Изменение по стране для сравнения: у денежных рядов — реальное."""
        if not self.is_money:
            return self.country_nominal_change
        if self.country_nominal_change is None:
            return None
        return real_change(self.country_real_ratio)

    @property
    def trend(self) -> str:
        """Изменение за пять лет словами и то же по стране."""
        if self.series.is_growth_index or self.past_value is None:
            return ""
        if self.break_year is not None:
            return gettext(
                "за %(years)s лет не сравнивается: разрыв сопоставимости в %(year)s году"
            ) % {
                "years": TREND_YEARS,
                "year": self.break_year,
            }
        if self.is_money:
            return self._money_trend()
        own = self.change
        if own is None:
            return ""
        text = gettext("%(change)s за %(years)s лет") % {"change": own.text, "years": TREND_YEARS}
        country = self.country_change
        if country is not None:
            text = gettext("%(own)s (по России — %(country)s)") % {
                "own": text,
                "country": country.text,
            }
        return text

    def _money_trend(self) -> str:
        """
        Изменение денежного ряда: реальное, а в скобках — в рублях.

        Без индекса за какой-то год называется только изменение в рублях.
        """
        nominal = self.nominal_change
        if nominal is None:
            return ""
        own = self.change
        if own is None:
            text = gettext("%(change)s в рублях за %(years)s лет") % {
                "change": nominal.text,
                "years": TREND_YEARS,
            }
            country_nominal = self.country_nominal_change
            if country_nominal is not None:
                text = gettext("%(own)s (по России — %(country)s)") % {
                    "own": text,
                    "country": country_nominal.text,
                }
            return text
        text = gettext("%(real)s за %(years)s лет (в рублях — %(nominal)s)") % {
            "real": own.text,
            "years": TREND_YEARS,
            "nominal": nominal.text,
        }
        country = self.country_change
        if country is not None:
            text = gettext("%(own)s; по России — %(country)s") % {
                "own": text,
                "country": country.text,
            }
        return text

    @property
    def trend_tone(self) -> str:
        """
        Оценка изменения: при известном изменении по стране — по разнице с ним.

        Без страны оценивается направление; рост дестимулятора — ухудшение.
        """
        own = self.change
        if own is None or self.polarity is None:
            return TONE_NEUTRAL
        country = self.country_change
        if country is not None:
            gap = own.amount - country.amount
            if abs(gap) < own.tolerance:
                return TONE_NEUTRAL
            better = gap > 0
        else:
            if own.direction == 0:
                return TONE_NEUTRAL
            better = own.direction > 0
        if self.polarity == "negative":
            better = not better
        return TONE_GOOD if better else TONE_BAD


@dataclass(slots=True)
class ThemeBlock:
    """Показатели одной темы в паспорте."""

    theme: FeaturedTheme
    positions: list[Position]

    # Сводка оценок для строки свёрнутой темы: «лучше большинства: 3 · хуже: 1».

    @property
    def better(self) -> int:
        """Сколько показателей темы лучше, чем в большинстве регионов."""
        return sum(1 for position in self.positions if position.tone == TONE_GOOD)

    @property
    def worse(self) -> int:
        """Сколько показателей темы хуже, чем в большинстве регионов."""
        return sum(1 for position in self.positions if position.tone == TONE_BAD)


@dataclass(slots=True)
class Passport:
    """Всё, что паспорт говорит о территории словами."""

    positions: list[Position]
    strengths: list[Position]
    weaknesses: list[Position]
    themes: list[ThemeBlock]
    total: int

    def by_key(self) -> dict[str, Position]:
        """Положение по ключу ряда."""
        return {position.series.key: position for position in self.positions}

    @property
    def discontinued(self) -> list[Position]:
        """Показатели, публикацию которых источник прекратил."""
        return [
            position
            for position in self.positions
            if position.status is not None and position.status.is_discontinued
        ]

    @property
    def summary(self) -> list[str]:
        """Главное о регионе: численность, сильные и слабые стороны, полнота сведений."""
        lines: list[str] = []
        population = next(
            (position for position in self.positions if position.series.role == "population"), None
        )
        if population is not None:
            line = gettext("Население — %(value)s %(unit)s, %(place)s") % {
                "value": ru_number(population.value, population.series.precision),
                "unit": population.series.unit_label,
                "place": population.place_text,
            }
            if population.trend:
                line = f"{line}; {population.trend}"
            lines.append(f"{line}.")

        if self.strengths:
            lines.append(
                gettext("Сильнее всего регион выглядит по показателям: %(items)s.")
                % {"items": _enumerate(self.strengths[:SUMMARY_LIMIT])}
            )
        else:
            lines.append(
                gettext("Ни по одному основному показателю регион не входит в число лучших.")
            )

        if self.weaknesses:
            lines.append(
                gettext("Слабее всего — по показателям: %(items)s.")
                % {"items": _enumerate(self.weaknesses[:SUMMARY_LIMIT])}
            )
        else:
            lines.append(
                gettext("Ни по одному основному показателю регион не входит в число худших.")
            )

        if self.baskets:
            lines.append(self.baskets)

        if self.positions:
            lines.append(
                gettext(
                    "Сведения есть по %(count)s из %(total)s основных показателей, "
                    "самые свежие — за %(year)s год."
                )
                % {
                    "count": len(self.positions),
                    "total": self.total,
                    "year": max(position.year for position in self.positions),
                }
            )
        return lines

    @property
    def baskets(self) -> str:
        """
        Доход в фиксированных наборах одной фразой: сколько наборов на доход и зарплату.

        Набор Росстата составлен для сравнения регионов; делится значение того же года.
        Это расчёт проекта, а не показатель Росстата «покупательная способность»: у него
        товарные эквиваленты дохода по отдельным товарам.
        """
        by_key = self.by_key()
        basket = by_key.get(BASKET_COST)
        if basket is None or basket.value <= 0:
            return ""
        found: dict[str, tuple[float, float | None]] = {}
        for role, key in (("income", BASKET_INCOME), ("wage", BASKET_WAGE)):
            position = by_key.get(key)
            if position is None or position.year != basket.year:
                continue
            country = None
            if position.country_value and basket.country_value:
                country = position.country_value / basket.country_value
            found[role] = (position.value / basket.value, country)
        if not found:
            return ""

        def number(value: float) -> str:
            return ru_number(value, BASKET_DIGITS)

        if "income" in found and "wage" in found:
            (income, income_country), (wage, wage_country) = found["income"], found["wage"]
            text = gettext(
                "На средний доход можно купить %(income)s фиксированного набора товаров "
                "и услуг, на среднюю зарплату — %(wage)s"
            ) % {"income": number(income), "wage": number(wage)}
            if income_country is not None and wage_country is not None:
                text = gettext("%(own)s (по России — %(income)s и %(wage)s)") % {
                    "own": text,
                    "income": number(income_country),
                    "wage": number(wage_country),
                }
        else:
            role, (value, versus) = next(iter(found.items()))
            if role == "income":
                template = gettext(
                    "На средний доход можно купить %(value)s фиксированного набора товаров и услуг"
                )
            else:
                template = gettext(
                    "На среднюю зарплату можно купить %(value)s фиксированного набора "
                    "товаров и услуг"
                )
            text = template % {"value": number(value)}
            if versus is not None:
                text = gettext("%(own)s (по России — %(country)s)") % {
                    "own": text,
                    "country": number(versus),
                }
        return gettext("%(text)s; %(year)s год, расчёт RegionLens.") % {
            "text": text,
            "year": basket.year,
        }


def _lower_first(title: str) -> str:
    """Строчная первая буква названия внутри фразы; сокращения («ВРП …») не меняются."""
    if len(title) > 1 and title[1].isupper():
        return title
    return title[:1].lower() + title[1:]


def _enumerate(positions: list[Position]) -> str:
    """«а, б и в» — перечень названий внутри фразы."""
    titles = [_lower_first(position.series.short_title) for position in positions]
    if len(titles) == 1:
        return titles[0]
    return gettext("%(head)s и %(last)s") % {"head": ", ".join(titles[:-1]), "last": titles[-1]}


@dataclass(slots=True)
class _Breaks:
    """Годы разрывов одного ряда, разделённые по тому, чьё изменение они прерывают."""

    own: list[int]
    country: list[int]


def _breaks(keys: list[str], territory_code: str) -> dict[str, _Breaks]:
    """
    Разрывы сопоставимости по рядам — что они прерывают для региона и для страны.

    Смена состава территорий меняет значение по России, но не значения субъекта,
    если разрыв не отнесён к нему явно.
    """
    found: dict[str, _Breaks] = {}
    rows = SeriesBreak.objects.filter(series__key__in=keys).values_list(
        "series__key", "year", "kind", "territory__code"
    )
    for key, year, kind, code in rows:
        entry = found.setdefault(key, _Breaks(own=[], country=[]))
        if code is not None:
            if code == territory_code:
                entry.own.append(year)
        elif kind == BreakKind.TERRITORY:
            entry.country.append(year)
        else:
            entry.own.append(year)
            entry.country.append(year)
    return found


def build_passport(territory_code: str) -> Passport:
    """Собрать положение территории по основному набору и выводы из него."""
    featured = featured_set()
    catalogue = featured.by_key()
    keys = [item.key for item in featured.series]
    breaks = _breaks(keys, territory_code)
    values = deflators(featured.series, [territory_code, COUNTRY_CODE])

    positions: list[Position] = []
    for row in territory_positions(territory_code, keys):
        item = catalogue.get(row["series_key"])
        if item is None or row["value"] is None:
            continue
        position = _position(item, row)
        found = breaks.get(item.key)
        if found is not None and position.past_year is not None:
            window = range(position.past_year + 1, position.year + 1)
            own = [year for year in found.own if year in window]
            position.break_year = max(own) if own else None
            position.country_break = any(year in window for year in found.country)
        _apply_real(position, values, territory_code)
        positions.append(position)
    positions.sort(key=lambda position: position.series.order)

    assessed = [position for position in positions if position.is_assessed]
    strengths = sorted(
        (p for p in assessed if (p.favourable or 0) >= 1 - STRENGTH_SHARE),
        key=lambda p: (-(p.favourable or 0), p.series.order),
    )
    weaknesses = sorted(
        (p for p in assessed if (p.favourable or 0) <= STRENGTH_SHARE),
        key=lambda p: (p.favourable or 0, p.series.order),
    )

    by_theme = {theme.slug: ThemeBlock(theme=theme, positions=[]) for theme in featured.themes}
    for position in positions:
        block = by_theme.get(position.series.theme)
        if block is not None:
            block.positions.append(position)

    return Passport(
        positions=positions,
        strengths=strengths,
        weaknesses=weaknesses,
        themes=[block for block in by_theme.values() if block.positions],
        total=len(featured.series),
    )


def _apply_real(position: Position, values: Values, territory_code: str) -> None:
    """Пересчитать изменение денежного ряда за пять лет в неизменные цены — региона и страны."""
    past = position.past_year
    if position.series.real is None or past is None:
        return
    if position.past_value:
        position.real_ratio = real_between(
            position.series,
            values,
            territory_code,
            start=past,
            end=position.year,
            nominal_ratio=position.value / position.past_value,
        )
    if position.country_value and position.country_past_value:
        position.country_real_ratio = real_between(
            position.series,
            values,
            COUNTRY_CODE,
            start=past,
            end=position.year,
            nominal_ratio=position.country_value / position.country_past_value,
        )


def position_for(item: FeaturedSeries, territory_code: str) -> Position | None:
    """Положение территории по одному ряду, в том числе вне основного набора, без разрывов."""
    rows = territory_positions(territory_code, [item.key])
    if not rows or rows[0]["value"] is None:
        return None
    return _position(item, rows[0])


def _position(item: FeaturedSeries, row: dict[str, Any]) -> Position:
    """Положение из строки выборки склада."""
    return Position(
        series=item,
        year=row["year"],
        value=row["value"],
        rank_desc=row["rank_desc"],
        rank_asc=row["rank_asc"],
        percentile=row["percentile"] or 0.0,
        territories=row["territories"],
        country_value=row.get("country_value"),
        past_year=row.get("past_year"),
        past_value=row.get("past_value"),
        country_past_value=row.get("country_past_value"),
        flags=int(row.get("flags") or 0),
    )
