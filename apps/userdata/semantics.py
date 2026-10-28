"""
Смысл величины своих данных и одна таблица правил «что с каким типом можно».

Тип величины: поток (сколько произошло за период), запас (сколько есть на дату), сумма —
складывается по регионам, а за период она или на дату, не указано, — относительная (доля,
среднее, на жителя) и индекс (изменение к другому периоду). У потока, запаса и суммы есть
основа сравнения регионов: жители, площадь или «не делить» (объекты территории — посты
наблюдения, ООПТ).

Окно «Добавить карточку», каталог «Расчёт показателя», ответы карточек, пересчёты сборки,
свёртка месяцев и формула спрашивают только эту таблицу (``verdict``). Тип угадывается
по названию и единице (``guess``), решение человека хранится в описании показателя.
Прежний вид «сумма» и «относительная» уточняется при чтении (``refine``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from django.utils.functional import Promise
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.catalog.constants import is_unnormalised_title

FLOW, STOCK, SUM, RELATIVE, INDEX = "flow", "stock", "sum", "relative", "index"
TYPES = (FLOW, STOCK, SUM, RELATIVE, INDEX)
# Складываются по регионам: поток, запас и сумма без уточнения.
ADDITIVE = frozenset({FLOW, STOCK, SUM})
# Уровни, а не изменения: всё, кроме индекса.
LEVELS = ADDITIVE | {RELATIVE}
EVERY = frozenset(TYPES)
PEOPLE, AREA, NONE = "people", "area", "none"
BASES = (PEOPLE, AREA, NONE)
BASE_CHOICES: tuple[tuple[str, Promise], ...] = (
    (PEOPLE, _("жителей")),
    (AREA, _("площадь")),
    (NONE, _("не делить")),
)
# Что под подписью типа в поле «Что это за величина».
TYPE_HINTS: dict[str, Promise] = {
    FLOW: _("Складывается по регионам и по месяцам: выдано, произведено, зарегистрировано"),
    STOCK: _("Складывается по регионам, но не по месяцам: остаток, численность, задолженность"),
    SUM: _("Складывается по регионам; за период или на дату — не указано"),
    RELATIVE: _("Не складывается: %, средняя зарплата, на 100 000 жителей"),
    INDEX: _("% к прошлому году, к декабрю, «2020 = 100»"),
}

# Свёртка месяцев в год: сумма месяцев, среднее за месяц, декабрь, «январь–декабрь».
MONTHS_SUM, MONTHS_MEAN, MONTHS_DECEMBER, MONTHS_YTD = "sum", "mean", "december", "ytd"
MONTHS_BY_TYPE: dict[str, tuple[str, ...]] = {
    FLOW: (MONTHS_SUM, MONTHS_YTD),
    STOCK: (MONTHS_DECEMBER, MONTHS_MEAN),
    SUM: (MONTHS_SUM, MONTHS_MEAN, MONTHS_DECEMBER, MONTHS_YTD),
    RELATIVE: (MONTHS_MEAN, MONTHS_DECEMBER, MONTHS_YTD),
    INDEX: (MONTHS_YTD,),
}
# Ответ о месяцах уточняет сумму: месяцы складываются — поток, берётся декабрь или
# среднее остатков — запас.
MONTHS_TYPE = {
    MONTHS_SUM: FLOW,
    MONTHS_YTD: FLOW,
    MONTHS_DECEMBER: STOCK,
    MONTHS_MEAN: STOCK,
}

# Пересчёты сборки — тип результата.
PER_1, PER_1000, PER_10000, PER_100000 = "per1", "per1000", "per10000", "per100000"
PER_KM2 = "perkm2"
PER_PEOPLE = (PER_1, PER_1000, PER_10000, PER_100000)
DERIVED_TYPES = {
    **dict.fromkeys((*PER_PEOPLE, PER_KM2, "share", "russia"), RELATIVE),
    "growth": INDEX,
}

# --- Угадывание по названию и единице ------------------------------------------------------

# Индекс: отношение к другому периоду. «% к общему числу» — доля, а не индекс;
# «Россия = 100» — сравнение со страной, а не с другим годом.
_INDEX = re.compile(
    r"(%|процент\w*)\s*к\s+(предыдущ|соответствующ|декабр|аналогичн|прошл|базов|январ|"
    r"уровню\s+\d{4}|\d{4})|к\s+предыдущему\s+(году|месяцу|периоду|кварталу)|"
    r"к\s+соответствующему\s+(периоду|месяцу|кварталу)|\d{4}\s*(г\.?\s*)?=\s*100|"
    r"индекс\w*\s+(потребительск|цен|производств|промышленн|физическ|тариф|реальн)|"
    r"темп\w*\s+(роста|прироста)",
    re.IGNORECASE,
)
# Единица относительной величины: доля, коэффициент, на жителя, годы жизни.
_RELATIVE_UNIT = re.compile(
    r"(%|процент|промилле|‰|на\s*1\b|на\s*10\b|на\s*1\s?000|на\s*10\s?000|на\s*100|на душу|"
    r"на жител|на одного|на 1 жител|коэффициент|индекс|балл|\bлет\b|\bгод(а|ы|ов)?\b|\bраз\b|"
    r"= ?100|per\s|percent|ratio|index|years)",
    re.IGNORECASE,
)
# Название относительной величины: среднее (не среднегодовая численность — она складывается;
# не «средней тяжести» и не «малого и среднего бизнеса»), доля, уровень, отношение (не «в
# отношении кого-то»), на жителя, цена набора.
_RELATIVE_NAME = re.compile(
    r"(средн(?!егодов|есписочн|ей тяжест|его бизнес|его предпринимат)|в среднем|на душу|"
    r"на жител|на одного|на 1 |на 10 |на 100|"
    r"на 1 ?000|доля|удельн|уровень|коэффициент|индекс|инденкс|темп|(?<!в )отношени|"
    r"плотност|в %|в процент|% к |обеспеченност|стоимост\w*\s+(\S+\s+)?набора|\bцен[аы]\b|"
    r"продолжительност|average|mean|per capita|share|rate|ratio)",
    re.IGNORECASE,
)
# Единица суммы: люди, штуки, деньги и объёмы целиком.
_SUM_UNIT = re.compile(
    r"(человек|чел\.|единиц|штук|шт\.|тонн|\bт\b|млн|млрд|тыс|кв\.\s?м|\bга\b|гектар|"
    r"случа|people|persons|units|cases|thousand|million|billion)",
    re.IGNORECASE,
)
# Название суммы, когда единицы нет: численность, количество, число, объём, оборот.
_SUM_NAME = re.compile(r"(численност|количеств|^число |объ[её]м|оборот)", re.IGNORECASE)
# Рубли без «тыс.», «млн», «млрд» — сумма на человека: зарплата, пенсия, прожиточный минимум.
_PLAIN_RUBLES = re.compile(r"^\s*(руб\.?|рубл\w*|₽)\s*$", re.IGNORECASE)
# Дата в названии («на 1 января 2024 года», «на 01.01.2024», «на 10-е марта») — не признак
# величины «на 1 жителя»: её убирают до сравнения с признаками относительной величины.
_DATE = re.compile(
    r"на\s+\d{1,2}(?:-?(?:е|го))?\s+(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|"
    r"сентябр|октябр|ноябр|декабр)\w*(?:\s+\d{4}(?:\s*(?:года|г\.?))?)?"
    r"|на\s+\d{1,2}\.\d{1,2}(?:\.\d{2,4})?",
    re.IGNORECASE,
)
# Запас по дате: «на 1 января», «на начало года», «на конец месяца», «по состоянию на».
_STOCK_DATE = re.compile(
    r"на\s+(1|01|первое)\s*(январ|\.01)|на\s+начало|на\s+конец|по\s+состоянию|конец\s+(года|"
    r"периода|месяца)|на\s+\d{1,2}(-?(е|го))?\s+(январ|феврал|март|апрел|ма[яй]|июн|июл|"
    r"август|сентябр|октябр|ноябр|декабр)|на\s+\d{1,2}\.\d{1,2}",
    re.IGNORECASE,
)
# Запас на начало года: делится на численность на 1 января.
_YEAR_START = re.compile(r"на\s+(1|01|первое)\s*(январ|\.01)|на\s+начало\s+года", re.I)
# Поток: события и объёмы за период.
_FLOW = re.compile(
    r"за\s+(год|период|январ|\d{4})|январ\w*\s*[-–—]|в\s+течение\s+года|выдан|введен|ввод\b|"
    r"образован|родивш|родил|умерш|умерл|прибывш|выбывш|зарегистрирован|выявлен|обследован|"
    r"оборот|объ[её]м|производств|произведен|выпуск|добыч|выброс|доход|расход|поступлен|"
    r"налог|инвестиц|валов|добавленн|продаж|экспорт|импорт|заготов|собран|перевез|отгруж|"
    r"выдач|начислен|выплачен|выплат|использован|забор|забрано|сброс|ущерб|преступлен|"
    r"правонарушен|случа|потреблен|построен|строительств|принят|направлен|реализ|браков|"
    r"развод|вызов|посещени|обращени|пожар|дорожно-транспорт|смерт|рождени|миграц|прирост|"
    r"убыл|перевозк|грузооборот|пассажирооборот|улов|урожа|сбор\b|прибыл|сальдо|субсиди|"
    r"трансферт|дотаци|закуп|уволен|высвобожд|раскрыт|расследован|захорон|рожденн",
    re.IGNORECASE,
)
# Запас: то, что есть на дату.
_STOCK = re.compile(
    r"накоплен|состоят|остат|задолженност|численност|среднесписочн|поголовь|\bфонд|\bпарк\b|"
    r"протяж[её]нност|площад|мощност|вклад|депозит|портфел|наличи|запас|"
    r"(число|количество)\s+(\S+\s+)?(организаци|предприяти|учреждени|объект|пост|створ|станци|"
    r"город|населённ|населенн|коек|мест\b|школ|больниц|врач|скважин|ООПТ|особо\s+охраня|пункт|"
    r"субъект|хозяйств|автомобил|библиотек|музе|театр|аптек|\S*\s*свал)",
    re.IGNORECASE,
)
# Событие сильнее «численности»: «численность выбывших» — поток.
_EVENT = re.compile(
    r"выбывш|принят|прибывш|родивш|умерш|зарегистрирован|выявлен|уволен|направлен|обследован",
    re.I,
)
# Единица в хвосте названия, когда своей нет: «Транспорт; тыс. рублей».
_NAME_TAIL = re.compile(r"[;,]\s*([^;,]+)$")
# Остаток сильнее слов потока: «задолженность по выплатам» — запас.
_HOLDING = re.compile(r"накоплен|состоят|остат|задолженност|на\s+конец|на\s+начало", re.I)
# Объекты территории: на жителей их не делят.
_NO_BASE = re.compile(
    r"пост\w*\s+(наблюд|мониторинг)|створ|станци\w*\s+(наблюд|мониторинг|контрол)|ООПТ|"
    r"особо\s+охраня|месторожден|водн\w+\s+объект|"
    r"(число|количество)\s+(\S+\s+){0,2}(город|населённ|населенн|муниципальн|субъект)",
    re.IGNORECASE,
)
# Сама численность населения и протяжённость дорог сравниваются на км².
_AREA_BASE = re.compile(
    r"^\s*(среднегодов\w+\s+)?(численность\s+(постоянного\s+|всего\s+)?населения|население|"
    r"(regions?\s+)?population)\s*($|[,(]|на\s|всего|оценка)|"
    r"протяж[её]нност\w*\s+(\S+\s+){0,3}(дорог|путей|сет)",
    re.IGNORECASE,
)
# Рубли в месяц на человека: только их делят на прожиточный минимум.
_MONTHLY = re.compile(
    r"в\s+месяц|среднемесячн|ежемесячн|месячн\w*\s+(заработ|доход|пенси)|/\s*мес", re.I
)
# Объёмы, которые естественно делить на одного жителя: площадь жилья, тонны, кубометры.
_VOLUME = re.compile(r"кв\.?\s*м|м2|м²|тонн|\bт\b|куб|м3|м³|\bкг\b|литр|квт|гектар|\bга\b", re.I)
_RUBLES = re.compile(r"(руб|₽|\brub)", re.IGNORECASE)
_CURRENCY = re.compile(r"(руб|₽|\brub|долл|\$|\busd\b|евро|€|\beur\b)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Guess:
    """Тип и основа по названию; ``sure`` — вид подсказан признаками, а не выбран наугад."""

    type: str
    base: str
    sure: bool


def without_dates(text: str) -> str:
    """Название или единица без дат: «Численность на 1 января» → «Численность »."""
    return _DATE.sub(" ", text or "")


def guess(name: str, unit: str) -> Guess:  # noqa: PLR0911 — признаки по порядку
    """
    Тип величины по названию и единице: индекс, относительная, запас по дате, поток или
    запас по словам, сумма по единице. Без признаков — сумма или относительная, наугад.
    """
    name, unit = name or "", unit or ""
    text = f"{name} {unit}"
    base = guess_base(name)
    if _INDEX.search(text):
        return Guess(INDEX, base, True)
    undated_name, undated_unit = without_dates(name), without_dates(unit)
    if (
        _PLAIN_RUBLES.match(undated_unit)
        or _RELATIVE_UNIT.search(undated_unit)
        or _RELATIVE_NAME.search(undated_name)
    ):
        return Guess(RELATIVE, base, True)
    if _STOCK_DATE.search(text):
        return Guess(STOCK, base, True)
    flow, stock = _FLOW.search(name), _STOCK.search(name)
    if flow and stock:
        if _EVENT.search(name):
            return Guess(FLOW, base, True)
        return Guess(STOCK if _HOLDING.search(name) else SUM, base, True)
    if flow:
        return Guess(FLOW, base, True)
    if stock:
        return Guess(STOCK, base, True)
    tail = _NAME_TAIL.search(name)
    if _SUM_UNIT.search(unit or (tail.group(1) if tail else "")) or _SUM_NAME.search(name):
        return Guess(SUM, base, True)
    return Guess(SUM if is_unnormalised_title(text, "unknown") else RELATIVE, base, False)


def guess_base(name: str) -> str:
    """Основа сравнения по названию: объекты территории — не делить, население — площадь."""
    if _NO_BASE.search(name or ""):
        return NONE
    if _AREA_BASE.search(name or ""):
        return AREA
    return PEOPLE


def refine(kind: str, name: str, unit: str) -> str:
    """
    Прежний вид «сумма» или «относительная» — тип: сумма уточняется до потока или запаса,
    относительная — до индекса, если признаки названия ясны. Иначе вид остаётся как был.
    """
    if kind not in {SUM, RELATIVE}:
        return kind if kind in TYPES else ""
    found = guess(name, unit).type
    if kind == SUM and found in {FLOW, STOCK}:
        return found
    if kind == RELATIVE and found == INDEX:
        return INDEX
    return kind


def series_type(
    kind: str, *, derived: str = "", name: str = "", unit: str = "", chosen: str = ""
) -> str:
    """
    Тип ряда: у пересчёта — по пересчёту (выбор человека относится к исходному показателю:
    «на жителя» от суммы — уже относительная); иначе выбранный человеком; иначе прежний вид,
    уточнённый по названию.
    """
    if derived in DERIVED_TYPES:
        return DERIVED_TYPES[derived]
    if chosen in TYPES:
        return chosen
    return refine(kind, name, unit) or RELATIVE


def official_type(*, is_sum: bool, unit_kind: str) -> str:
    """
    Тип ряда сайта: индекс — по категории единицы справочника, сумма — как решил справочник
    (абсолютная величина или деньги с разрядом), остальное — относительная. Поток или запас
    у рядов сайта не различаются: сайт их не пересчитывает.
    """
    if unit_kind == "index":
        return INDEX
    return SUM if is_sum else RELATIVE


def is_additive(kind: str) -> bool:
    """Складывается по регионам: поток, запас или сумма."""
    return kind in ADDITIVE


# Единица из одного разряда («тыс.», «млн») о деньгах не говорит — смотрят и название.
_ORDER_ONLY = re.compile(r"^\s*(?:тыс|млн|млрд|трлн)\.?\s*$", re.IGNORECASE)


def _money_text(name: str, unit: str) -> str:
    """
    Где искать признак денег: в единице, если она указана; в названии — только без единицы
    или при единице из одного разряда. «Срок кредитов в рублях», месяцев, — не деньги.
    """
    unit = (unit or "").strip()
    if unit and not _ORDER_ONLY.match(unit):
        return unit
    return f"{unit} {name}"


def is_rubles(name: str, unit: str) -> bool:
    """Рубли — по единице, без неё по названию: в цены года их переводит индекс цен."""
    return bool(_RUBLES.search(_money_text(name, unit)))


def is_currency(name: str, unit: str) -> bool:
    """Деньги в любой валюте — по единице, без неё по названию."""
    return bool(_CURRENCY.search(_money_text(name, unit)))


def is_monthly(name: str, unit: str) -> bool:
    """Значение в месяц: «в месяц», «среднемесячная»."""
    return bool(_MONTHLY.search(f"{name} {unit}"))


def starts_year(name: str) -> bool:
    """Запас на начало года: «на 1 января», «на начало года»."""
    return bool(_YEAR_START.search(name or ""))


def months_methods(kind: str, options: list[str] | tuple[str, ...]) -> list[str]:
    """Способы свёртки месяцев в год, возможные по периодам таблицы и подходящие типу."""
    allowed = MONTHS_BY_TYPE.get(kind, ())
    return [method for method in options if method in allowed]


def suggested_per(kind: str, base: str, name: str, unit: str) -> str:
    """
    Какой пересчёт на жителей советовать сумме: деньгам и объёмам — на жителя, людям
    и событиям — на 100 000 жителей; при другой основе — никакой.
    """
    if kind not in ADDITIVE or base != PEOPLE:
        return ""
    if is_currency(name, unit) or _VOLUME.search(unit or ""):
        return PER_1
    return PER_100000


# --- Таблица правил ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Value:
    """Что о ряде нужно правилам: тип, основа, деньги, годы, регионы, части."""

    type: str
    base: str = PEOPLE
    is_rubles: bool = False
    is_currency: bool = False
    # Рубли в месяц на человека.
    monthly: bool = False
    years: int = 0
    regions: int = 0
    # Возможных частей структуры — сумм той же единицы.
    parts: int = 0
    is_user: bool = True

    @property
    def is_sum(self) -> bool:
        return self.type in ADDITIVE

    @property
    def country_only(self) -> bool:
        """Своя таблица, где у показателя есть годы, но нет ни одного региона."""
        return self.is_user and self.years > 0 and self.regions == 0


@dataclass(frozen=True, slots=True)
class Verdict:
    """Можно ли: пустая причина — можно. ``via`` — пересчёт, через который сумме можно."""

    reason: str = ""
    via: str = ""

    def __bool__(self) -> bool:
        return not self.reason


@dataclass(frozen=True, slots=True)
class Rule:
    """
    Правило действия: типы, которым можно; суммам — через пересчёт на основу (``via``);
    основы, при которых можно (пересчёты на жителей); деньги; условия данных.
    """

    types: frozenset[str]
    via: bool = False
    reasons: Mapping[str, Promise] = field(default_factory=dict)
    bases: frozenset[str] = frozenset(BASES)
    rubles: bool = False
    monthly: bool = False
    years: int = 1
    regions: int = 0
    region: bool = False
    parts: int = 0
    user: bool = False
    # Работает и со значением по России, без регионов.
    country: bool = False


# Регионов меньше этого: неравенство и пространственный анализ не считаются.
FEW_REGIONS = 20
# Сближение оценивается на отрезке не короче пяти лет — нужно шесть лет значений.
CONVERGENCE_YEARS = 6

_SUMS_ONLY = _("только для сумм по регионам")
_RELATIVE_ONLY = _("только для относительных величин")
_INDEX_NOT = _("у индекса не считается: он уже показывает изменение к другому периоду")
_LEVELS_ONLY = _("разрыв и сближение меряют по уровням, а не по темпам")
_CONTRIBUTIONS = _(
    "вклады регионов складываются только у сумм: у относительной величины смотрите "
    "«Темпы роста по регионам»"
)
_SHARES = _("доли считаются у сумм")
_RATE_OF_RATE = _("темп от темпа не считается")
_RUSSIA_SHARE = _("у суммы это доля в значении по России: смотрите «Долю»")
_PEOPLE = frozenset({PEOPLE})

RULES: dict[str, Rule] = {
    # Посмотреть.
    "view:map": Rule(EVERY),
    "view:compare": Rule(EVERY, years=2),
    "view:rankings": Rule(EVERY),
    "view:distribution": Rule(EVERY),
    "view:table": Rule(EVERY),
    "answer:multiples": Rule(EVERY, years=2),
    "view:structure": Rule(
        ADDITIVE, reasons=dict.fromkeys((RELATIVE, INDEX), _SHARES), parts=2, user=True
    ),
    # Ответить на вопрос.
    "answer:notable": Rule(EVERY),
    "answer:leaders": Rule(EVERY),
    "answer:mine": Rule(EVERY, region=True),
    "answer:change": Rule(EVERY, years=2, country=True),
    "answer:growth": Rule(EVERY, years=2),
    "answer:contributions": Rule(
        ADDITIVE, reasons=dict.fromkeys((RELATIVE, INDEX), _CONTRIBUTIONS), years=2
    ),
    "answer:districts": Rule(EVERY),
    "answer:spread": Rule(
        frozenset({RELATIVE}), via=True, reasons={INDEX: _LEVELS_ONLY}, regions=FEW_REGIONS
    ),
    "answer:convergence": Rule(
        frozenset({RELATIVE}),
        via=True,
        reasons={INDEX: _LEVELS_ONLY},
        regions=FEW_REGIONS,
        years=CONVERGENCE_YEARS,
    ),
    "answer:neighbours": Rule(frozenset({RELATIVE, INDEX}), via=True, regions=FEW_REGIONS),
    "answer:related": Rule(frozenset({RELATIVE, INDEX}), via=True, regions=FEW_REGIONS),
    "answer:structure": Rule(ADDITIVE, reasons=dict.fromkeys((RELATIVE, INDEX), _SHARES)),
    "answer:group": Rule(EVERY),
    # Сравнить.
    "answer:comparison": Rule(EVERY),
    "answer:relation": Rule(frozenset({RELATIVE, INDEX}), via=True, regions=FEW_REGIONS),
    # Сразу несколько карточек.
    "set:profile": Rule(EVERY),
    "set:pair": Rule(EVERY),
    # Новый показатель: каталог расчётов и пересчёты сборки.
    "calc:per1": Rule(ADDITIVE, bases=_PEOPLE),
    "calc:per1000": Rule(ADDITIVE, bases=_PEOPLE),
    "calc:per10000": Rule(ADDITIVE, bases=_PEOPLE),
    "calc:per100000": Rule(ADDITIVE, bases=_PEOPLE),
    "calc:perkm2": Rule(ADDITIVE),
    "calc:pergroup": Rule(ADDITIVE, bases=_PEOPLE),
    "calc:pricelevel": Rule(frozenset({RELATIVE}), rubles=True),
    "calc:subsistence": Rule(frozenset({RELATIVE}), rubles=True, monthly=True),
    "calc:share": Rule(ADDITIVE),
    "calc:structure": Rule(ADDITIVE),
    "calc:hhi": Rule(ADDITIVE),
    "calc:growth": Rule(LEVELS, reasons={INDEX: _RATE_OF_RATE}, years=2),
    "calc:baseyear": Rule(LEVELS, years=2),
    "calc:between": Rule(LEVELS, years=2),
    "calc:annualrate": Rule(LEVELS, years=3),
    "calc:real": Rule(LEVELS, rubles=True),
    "calc:russia": Rule(frozenset({RELATIVE}), reasons=dict.fromkeys(ADDITIVE, _RUSSIA_SHARE)),
    "calc:russiadeviation": Rule(frozenset({RELATIVE, INDEX})),
    "calc:districtdeviation": Rule(frozenset({RELATIVE, INDEX})),
    "calc:sum": Rule(ADDITIVE),
    "calc:lq": Rule(ADDITIVE),
    "calc:structuralshift": Rule(ADDITIVE, years=2),
    "calc:shiftshare": Rule(ADDITIVE, years=2),
    # Сборка: сумма по разрезу, область без округов, «Россия» как сумма субъектов.
    "build:fold": Rule(ADDITIVE),
}


def verdict(  # noqa: PLR0911 — проверки по порядку
    action: str, value: Value, *, region_known: bool = True, data: bool = True
) -> Verdict:
    """
    Можно ли действие ``action`` с рядом ``value``. ``data=False`` — только тип и основа:
    условия данных (годы, регионы, мой регион, части) не проверяются.
    """
    rule = RULES[action]
    if value.country_only and not rule.country:
        return Verdict(gettext("в таблице только значение по России, регионов нет"))
    if rule.user and not value.is_user:
        return Verdict(gettext("структура — для своих таблиц"))
    if value.type not in rule.types:
        if rule.via and value.is_sum:
            return _via(value)
        return Verdict(str(rule.reasons.get(value.type) or _type_reason(value)))
    if value.is_sum and value.base not in rule.bases:
        return Verdict(_base_reason(value.base))
    if rule.rubles and not value.is_rubles:
        return Verdict(gettext("только для показателей в рублях"))
    if rule.monthly and not value.monthly:
        return Verdict(gettext("только для рублей в месяц на человека"))
    if data:
        return Verdict(_data_reason(rule, value, region_known=region_known))
    return Verdict()


def _type_reason(value: Value) -> str:
    """Почему тип не подходит, если у правила нет своей причины."""
    if value.type == INDEX:
        return str(_INDEX_NOT)
    if value.is_sum:
        return str(_RELATIVE_ONLY)
    return str(_SUMS_ONLY)


def _base_reason(base: str) -> str:
    if base == AREA:
        return gettext("основа сравнения — площадь: пересчитайте на км²")
    return gettext("значения не зависят от числа жителей: на жителей не пересчитываются")


def _via(value: Value) -> Verdict:
    """Сумма — только через пересчёт на основу; без основы — нельзя."""
    if value.base == NONE:
        return Verdict(
            gettext("у показателя нет основы для сравнения регионов: значения показывают размер")
        )
    if value.base == AREA:
        if not value.is_user:
            return Verdict(gettext("для суммы считается по значению на км²"), via=PER_KM2)
        return Verdict(
            gettext("для суммы считается по значению на км²: сначала пересчитайте на км²"),
            via=PER_KM2,
        )
    if not value.is_user:
        return Verdict(
            gettext("для суммы считается по значению на жителя: выберите такой показатель сайта"),
            via=PER_1,
        )
    return Verdict(
        gettext("для суммы считается по значению на жителя: сначала пересчитайте на жителя"),
        via=PER_1,
    )


def _data_reason(rule: Rule, value: Value, *, region_known: bool) -> str:
    """Условия данных: годы, регионы, мой регион, части структуры."""
    if rule.years > 1 and value.years < rule.years:
        if value.years < 2:  # noqa: PLR2004 — один год
            return gettext("у показателя один год")
        return gettext("нужно лет не меньше: %(count)s") % {"count": rule.years}
    if rule.regions and value.regions < rule.regions:
        return gettext("нужны значения хотя бы по %(count)s регионам") % {"count": rule.regions}
    if rule.region and not region_known:
        return gettext("мой регион не выбран — выберите его в шапке сайта")
    if rule.parts and value.parts < rule.parts:
        return gettext("нужны две суммы в одной единице: части показателя или показатели таблицы")
    return ""


def sentence(reason: str) -> str:
    """Причина из таблицы правил — предложением для сообщения и карточки."""
    return f"{reason[:1].upper()}{reason[1:]}."


# Разряд в начале единицы: «млн руб.» — миллион рублей, «тыс. человек» — тысяча человек.
_UNIT_ORDER = re.compile(r"^(тыс|млн|млрд|трлн)(?:\.\s*|\s+)", re.IGNORECASE)
_ORDERS = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}


def unit_scale(unit: str) -> tuple[str, float]:
    """Единица без разряда и множитель разряда: «млн руб.» — («руб.», 1 000 000)."""
    text = " ".join(unit.casefold().split())
    found = _UNIT_ORDER.match(text)
    if found is None:
        return text, 1.0
    return text[found.end() :], _ORDERS[found.group(1)]


def parts_reason(kinds: list[str], units: list[str]) -> str:
    """
    Почему ряды нельзя считать частями одного целого: не суммы, суммы разных типов
    (поток с запасом) или разных единиц; пусто — можно. Разряд единицы («тыс.», «млн»)
    не мешает: сумма приводит слагаемые к единице первого.
    """
    if any(kind not in ADDITIVE for kind in kinds):
        return gettext("Части и слагаемые — только суммы: доли, средние и индексы не складываются.")
    exact = {kind for kind in kinds if kind != SUM}
    if len(exact) > 1:
        return gettext("Среди частей — значения за период и на дату: их нельзя складывать.")
    if len({unit_scale(unit)[0] for unit in units}) > 1:
        return gettext("Части и слагаемые — в одной единице: приведите их к одной.")
    return ""
