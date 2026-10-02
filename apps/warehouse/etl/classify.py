"""
Правила разбора свободного текста источника: единицы измерения, методические
примечания и направленность показателей.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from dataclasses import dataclass, field

from apps.catalog.constants import (
    SENTINEL_STRING_NO_DATA,
    SENTINEL_STRING_NOT_APPLICABLE,
    UNSPECIFIED_UNIT_NAME,
    BreakKind,
    Polarity,
    UnitKind,
)
from apps.catalog.units import short_unit_label
from apps.core.utils.text import normalize_text

# =======================================================================================
# Единицы измерения
# =======================================================================================

# Множители в полной и сокращённой форме; крупные раньше, иначе «миллиард» совпадёт
# с «миллион».
_MULTIPLIER_PATTERNS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"триллион|\bтрлн\b"), 1e12),
    (re.compile(r"миллиард|\bмлрд\b"), 1e9),
    (re.compile(r"миллион|\bмлн\b"), 1e6),
    (re.compile(r"тысяч|\bтыс\b"), 1e3),
)

# Составное объявление: «Миллионов рублей; для значений в целом по России: млрд руб».
_COUNTRY_UNIT_PATTERN = re.compile(
    r";\s*для\s+значений\s+(?:в\s+целом\s+)?по\s+России\s*:\s*(?P<unit>.+)$",
    re.IGNORECASE,
)

# Признаки категорий единиц измерения.
_CURRENCY_MARKERS = ("рубл", "доллар", "евро")
_INDEX_MARKERS = (
    "в процентах к предыдущ",
    "к декабрю предыдущ",
    "декабрь к декабрю",
    "в процентах к 20",
    "индекс",
    "в постоянных ценах, в процентах",
    "в сопоставимых ценах, в процентах",
)
_SHARE_MARKERS = ("в процентах", "процент", "в % ", "к итогу", "удельный вес")
_RATE_MARKERS = (
    "на 1000",
    "на 1 000",
    "на 10 000",
    "на 100 000",
    "на 100000",
    "на душу",
    "на одного",
    "на 10 тысяч",
    "на 100 тысяч",
    "в расчете на",
)
_PHYSICAL_MARKERS = (
    "тонн",
    "кубических метров",
    "квадратных метров",
    "километр",
    "гектар",
    "штук",
    "единиц",
    "литр",
    "центнер",
    "киловатт",
    "гигакалори",
    "мест",
    "коек",
    "экземпляр",
)
_PEOPLE_MARKERS = ("человек", "чел.", "работник", "занятых", "жител")

# Наибольшая длина фрагмента названия, восстанавливаемого как единица.
MAX_RECOVERED_UNIT_LENGTH = 60

# Признаки единицы во фрагменте после последней запятой. «Занятых» и «работник»
# сюда не входят: в формулировках это предмет счёта, а не единица.
_RECOVERABLE_UNIT_MARKERS = (
    *_CURRENCY_MARKERS,
    *_PHYSICAL_MARKERS,
    "человек",
    "чел.",
    "процент",
    "%",
)
# «на 1000 человек населения», «на 10000 человек населения соответствующего возраста».
_PER_POPULATION_PATTERN = re.compile(r"на\s+1[\d\s]*\s+(?:человек|жител)[^,;:()]*")
# Единица последним словом: «Распределение мигрантов человек»; число коек — в койках.
_TRAILING_UNIT_PATTERN = re.compile(r"(?:тыс\.?\s+)?человек$|(?<=^число больничных )коек")
# Единица в скобках в конце фрагмента: «…выданных организациями (единиц)».
_BRACKETED_PATTERN = re.compile(r"\(([^()]+)\)\s*$")
# Предлог, оставшийся от продолжения фразы: «на 100 000 человек населения от: болезней».
_DANGLING_PATTERN = re.compile(r"\s+(?:от|по|из|в|на|и)\s*$|\s+$")


@dataclass(slots=True)
class UnitClassification:
    """Результат классификации единицы измерения."""

    source_name: str
    code: str
    name_ru: str
    short_name_ru: str
    kind: str
    multiplier: float
    derived_from_name: bool
    # Коэффициент приведения значения по России в целом к единице измерения территорий.
    country_scale: float = 1.0
    # Признак составного объявления единицы, соотношение в котором определить не удалось.
    country_scale_unknown: bool = False


def classify_unit(
    source_name: object,
    *,
    indicator_name: object = "",
    subsection: object = "",
) -> UnitClassification:
    """
    Определить категорию единицы измерения и приведение значения по России.

    Единицу ``ND`` источник велит искать в ``indicator_name`` и ``subsection``.
    """
    original = normalize_text(source_name)
    raw, country_unit = _parse_compound_unit(original)
    derived = False

    if not raw or raw in {SENTINEL_STRING_NO_DATA, SENTINEL_STRING_NOT_APPLICABLE}:
        recovered = _recover_unit_from_text(subsection, indicator_name)
        raw = recovered or UNSPECIFIED_UNIT_NAME
        derived = True

    lowered = raw.lower()
    kind = _detect_unit_kind(lowered)
    multiplier = _detect_multiplier(lowered)
    country_scale, country_scale_unknown = _country_scale(multiplier, country_unit)

    return UnitClassification(
        source_name=original or SENTINEL_STRING_NO_DATA,
        # Восстановленная единица кодируется по полученному названию, а не по ``ND``.
        code=make_code("unit", raw if derived else (original or raw)),
        name_ru=raw,
        short_name_ru=_shorten_unit(raw),
        kind=kind,
        multiplier=multiplier or 1.0,
        derived_from_name=derived,
        country_scale=country_scale,
        country_scale_unknown=country_scale_unknown,
    )


def _country_scale(base_multiplier: float | None, country_unit: str) -> tuple[float, bool]:
    """
    Вычислить коэффициент приведения значения по России к единице территорий.

    Только при явных множителях обеих единиц; иначе соотношение помечается неизвестным.
    """
    if not country_unit:
        return 1.0, False

    country_multiplier = _detect_multiplier(country_unit.lower())
    if country_multiplier is None or base_multiplier is None:
        return 1.0, True

    return country_multiplier / base_multiplier, False


def _detect_unit_kind(lowered: str) -> str:  # noqa: PLR0911 - таблица решений
    """Определить категорию единицы по признакам в её названии."""
    # Индекс — раньше доли: «в процентах к предыдущему году» — индекс.
    if any(marker in lowered for marker in _INDEX_MARKERS):
        return UnitKind.INDEX
    if any(marker in lowered for marker in _RATE_MARKERS):
        return UnitKind.RATE
    if any(marker in lowered for marker in _CURRENCY_MARKERS):
        return UnitKind.CURRENCY
    if any(marker in lowered for marker in _SHARE_MARKERS):
        return UnitKind.SHARE
    if any(marker in lowered for marker in _PHYSICAL_MARKERS):
        return UnitKind.PHYSICAL
    if any(marker in lowered for marker in _PEOPLE_MARKERS):
        return UnitKind.ABSOLUTE
    return UnitKind.UNKNOWN


def _detect_multiplier(lowered: str) -> float | None:
    """Определить множитель приведения к базовой единице; ``None`` — множитель не указан."""
    for pattern, value in _MULTIPLIER_PATTERNS:
        if pattern.search(lowered):
            return value
    return None


def _parse_compound_unit(raw: str) -> tuple[str, str]:
    """Разделить составное объявление на единицу территорий и единицу для России (или пусто)."""
    match = _COUNTRY_UNIT_PATTERN.search(raw)
    if match is None:
        return raw, ""
    base = raw[: match.start()].strip().rstrip(",;").strip()
    return base, match.group("unit").strip()


def _shorten_unit(name: str) -> str:
    """Построить компактную подпись единицы по правилу ``apps.catalog.units``."""
    return short_unit_label(name)


def _recover_unit_from_text(subsection: object, indicator_name: object) -> str:
    """
    Восстановить единицу из конца формулировки разреза или показателя: «…, тыс. человек».

    Разрез и показатель проверяются порознь: склеенные, они дают «Тыс. человек Зоопарки».
    """
    for part in (normalize_text(subsection), normalize_text(indicator_name)):
        if part and (unit := _unit_in_phrase(part)):
            return unit[:1].upper() + unit[1:]
    return ""


def _unit_in_phrase(text: str) -> str:
    """Единица в одной формулировке или пустая строка."""
    if "," in text:
        # После двоеточия — уточнение разреза: «человек: женщины», «лет: 15-19».
        candidate = text.rsplit(",", 1)[-1].split(":", 1)[0].strip()
        if bracketed := _BRACKETED_PATTERN.search(candidate):
            candidate = bracketed.group(1)
        lowered = candidate.lower()
        if len(candidate) <= MAX_RECOVERED_UNIT_LENGTH and any(
            marker in lowered for marker in _RECOVERABLE_UNIT_MARKERS
        ):
            return candidate
    lowered = text.lower()
    for pattern in (_PER_POPULATION_PATTERN, _TRAILING_UNIT_PATTERN):
        if match := pattern.search(lowered):
            return _DANGLING_PATTERN.sub("", text[match.start() : match.end()])
    return ""


# =======================================================================================
# Методические примечания
# =======================================================================================

# Годы в примечании: одиночные и диапазоны через разные виды тире.
_YEAR_PATTERN = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_RANGE_PATTERN = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\s*[‒–—-]\s*(19[5-9]\d|20[0-4]\d)\b")
# Диапазон дат «на 1 января 2012 — на 1 января 2022» — как диапазон лет.
_DATE_RANGE_PATTERN = re.compile(
    r"на\s+1\s+января\s+(19[5-9]\d|20[0-4]\d)\s*(?:г\.?)?\s*[‒–—-]\s*"
    r"на\s+1\s+января\s+(19[5-9]\d|20[0-4]\d)",
    re.IGNORECASE,
)
# Срок документа («госпрограмма … на 2012–2020 годы») — не период данных.
_PLAN_RANGE_PATTERN = re.compile(
    r"\bна\s+(19[5-9]\d|20[0-4]\d)\s*[‒–—-]\s*(19[5-9]\d|20[0-4]\d)\b", re.IGNORECASE
)

# Формулировки, задающие момент изменения; «с» у источника бывает латинской, с датой —
# «с 1 июля 2012 г.».
_MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
_SINCE_PATTERN = re.compile(
    rf"(?:[сc]|начиная\s+с|начиная\s+со)\s+(?:\d{{1,2}}\s+(?:{_MONTHS})\s+)?"
    r"(19[5-9]\d|20[0-4]\d)\s*(?:г|год)",
    re.IGNORECASE,
)
_UNTIL_PATTERN = re.compile(r"до\s+(19[5-9]\d|20[0-4]\d)\s*(?:г|год)", re.IGNORECASE)
# «До 2012 г. включительно» — прежний порядок действовал и в самом 2012 году.
_UNTIL_INCLUSIVE_PATTERN = re.compile(
    r"до\s+(19[5-9]\d|20[0-4]\d)\s*(?:гг?\.?|года?)\s*включительно", re.IGNORECASE
)
# «С 2000 г. по 2008 г.» — порядок действовал по 2008 год включительно.
_FROM_TO_PATTERN = re.compile(
    r"[сc]\s+(19[5-9]\d|20[0-4]\d)\s*(?:г\.?|года?)?\s*по\s+(19[5-9]\d|20[0-4]\d)\s*(?:г|год)",
    re.IGNORECASE,
)
# Период в перечне «2000–2018 гг. — …. 2019, 2020 гг. — …»: годы, «г.» и тире или двоеточие
# в начале части или после знака препинания; «2021 г. — 0,4 %» — значение, а не период.
_PERIOD_HEAD_PATTERN = re.compile(
    r"(?:^|(?<=[.;,]))\s*(?:на\s+конец\s+)?"
    r"((?:19[5-9]\d|20[0-4]\d)(?:\s*(?:[‒–—-]|,)\s*(?:19[5-9]\d|20[0-4]\d))*)"
    r"\s*(?:гг?|годы?|года)\.?\s*[‒–—:-](?!\s*[−-]?\d)",
    re.IGNORECASE,
)

# Граница части примечания: Росстат склеивает сообщения через точку с запятой. «г.» перед
# названием города («по г. Санкт-Петербургу») предложение не заканчивает, «2017 г.» — может.
_CLAUSE_PATTERN = re.compile(r";|(?<=[.!?])(?<![^\d\s] [гГ]\.)\s+(?=[А-ЯЁA-Z])")

# Пересчёт, сделавший ряд сопоставимым («…пересчитаны с учётом итогов ВПН-2020»): разрыва нет.
_RECALCULATED_MARKER = "пересчитан"
# После диапазона: «с учетом итогов» переписи — те же пересчитанные данные, границы нет;
# ОКВЭД2 (ОК 029-2014) — диапазон уже по новому классификатору, граница в его начале.
_CENSUS_BASIS_MARKER = "с учетом итогов"
_NEW_CLASSIFIER_PATTERN = re.compile(r"оквэд\s*-?\s*2(?!\d)|ок\s*029-2014")

# Перемены в составе отдельных субъектов и субъекты, чьи значения они меняют; значения
# остальных субъектов и страны не меняются.
_SUBJECT_CHANGES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = tuple(
    (re.compile(pattern), codes)
    for pattern, codes in (
        # Автономные округа, вошедшие в края и области в 2005–2008 годах.
        (r"коми-пермяцк", ("RU-PER",)),
        (r"корякск", ("RU-KAM",)),
        (r"усть-ордынск", ("RU-IRK",)),
        (r"таймырск|эвенкийск", ("RU-KYA",)),
        (r"агинск", ("RU-ZAB",)),
        (r"включая\s+сведения\s+по\s+ненецк", ("RU-ARK",)),
        # Город внутри области: «включая г. Москву» — значение области с городом.
        (r"включая\s+(?:данные\s+)?(?:по\s+)?(?:г\.|городу?)\s*москв", ("RU-MOS",)),
        (r"включая\s+(?:данные\s+)?(?:по\s+)?(?:г\.|городу?)\s*санкт-петербург", ("RU-LEN",)),
        (r"включая\s+данные\s+по\s+ленинградской\s+области", ("RU-SPE",)),
        # Общая граница или единое отделение двух субъектов.
        (r"москв\w*\s+и\s+московской\s+области", ("RU-MOW", "RU-MOS")),
        (r"санкт-петербург\w*\s+и\s+ленинградской\s+области", ("RU-SPE", "RU-LEN")),
        (r"архангельской\s+области\s+и\s+ненецк", ("RU-ARK", "RU-NEN")),
        (r"хабаровск\w*\s+кра\w*\s+и\s+еврейск", ("RU-KHA", "RU-YEV")),
    )
)

# Признаки типов методических изменений по строчному тексту; проверяются по порядку.
_KIND_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile("|".join(markers)))
    for kind, markers in (
        (
            BreakKind.CLASSIFIER,
            ("оквэд", "окпд", "классификатор", "оксм", "окато на октмо", "новой редакции"),
        ),
        (
            BreakKind.TERRITORY,
            (
                r"состав\w* субъектов",
                r"федеральн\w* округ",
                r"указ\w* президента",
                r"в\w*ши\w* в (?:его )?состав",
                "с учетом состава",
                # «Включая данные по Ленинградской области»; «по ЗАТО», «по дирекции Фонда» — круг.
                r"включая данные по (?:\w+ )?(?:г\.|город|республик|област|кра|автономн|субъект)",
                # Субъекты и Байконур: их включение в итог меняет только значение по стране.
                "крым",
                "севастопол",
                "чеченск",
                "байконур",
                "донецк",
                "луганск",
                "запорожск",
                "херсонск",
            ),
        ),
        (
            BreakKind.OBSERVATION_START,
            (
                "наблюдение осуществляется с",
                "наблюдение проводится с",
                "обследование проводится с",
                "показатель рассчитывается с",
                "не рассчитывался",
                "не велось наблюдение",
                "не осуществлялось",
            ),
        ),
        (
            BreakKind.PRICE_BASE,
            ("в ценах", "в постоянных ценах", "базисный год", "сопоставимых ценах"),
        ),
        (
            BreakKind.COVERAGE,
            (
                "круг",
                "без учета",
                "с учетом",
                "включая",
                "не включая",
                "по полному кругу",
                # Возрастные границы обследования рабочей силы: 15–72 года, 15 лет и старше.
                r"в возрасте 15(?:[‒–—-]7\d| лет и старше)",
            ),
        ),
        (
            BreakKind.METHODOLOGY,
            (
                "методолог",
                "методик",
                "изменен",
                "пересмотр",
                "уточнен",
                "в возрасте",
                "по новой методике",
            ),
        ),
    )
)

# Указания на временную границу; без них примечание — пояснение к методике, не разрыв.
_TEMPORAL_MARKERS = (
    "до 20",
    "до 19",
    "с 20",
    "с 19",
    "начиная с",
    "за 20",
    "по 20",
    "ранее",
    "впервые",
)


@dataclass(frozen=True, slots=True)
class NoteBreak:
    """Год разрыва, вид изменения и субъект, если перемена касается только его."""

    year: int
    kind: str
    territory: str = ""


@dataclass(slots=True)
class NoteClassification:
    """Результат разбора методического примечания."""

    checksum: str
    text: str
    kind: str
    mentioned_years: list[int] = field(default_factory=list)
    breaks: list[NoteBreak] = field(default_factory=list)
    affects_comparability: bool = False

    @property
    def break_years(self) -> list[int]:
        """Годы разрыва по возрастанию."""
        return sorted({item.year for item in self.breaks})


def classify_note(text: object) -> NoteClassification | None:
    """Разобрать методическое примечание: тип изменения, упомянутые годы и годы разрыва."""
    normalized = normalize_text(text)
    if not normalized or normalized in {
        SENTINEL_STRING_NO_DATA,
        SENTINEL_STRING_NOT_APPLICABLE,
    }:
        return None

    lowered = normalized.lower()
    mentioned = _extract_years(normalized)
    kind = _detect_note_kind(lowered)
    has_temporal_marker = any(marker in lowered for marker in _TEMPORAL_MARKERS) or any(
        pattern.search(normalized) for pattern in (_SINCE_PATTERN, _DATE_RANGE_PATTERN)
    )

    # Нужны годы и временная граница; для общего типа — ещё и глагол изменения.
    affects = bool(mentioned) and has_temporal_marker
    if affects and kind == BreakKind.METHODOLOGY:
        affects = _has_change_verb(lowered)
    breaks = _extract_breaks(normalized, kind) if affects else []

    return NoteClassification(
        checksum=make_checksum(normalized),
        text=normalized,
        # Вид примечания в целом — по его отметкам, если они есть.
        kind=_prevailing_kind([item.kind for item in breaks]) if breaks else kind,
        mentioned_years=mentioned,
        breaks=breaks,
        affects_comparability=affects,
    )


def _detect_kind(lowered: str, *, skip: str = "") -> str:
    """Тип изменения по первому совпавшему признаку; без признаков — пустая строка."""
    for kind, pattern in _KIND_MARKERS:
        if kind != skip and pattern.search(lowered):
            return kind
    return ""


def _detect_note_kind(lowered: str) -> str:
    """Определить тип методического изменения по языковым признакам."""
    return _detect_kind(lowered) or BreakKind.METHODOLOGY


def _has_change_verb(lowered: str) -> bool:
    """Проверить наличие глагола изменения: отличает разрыв от общего пояснения."""
    verbs = (
        "изменен",
        "пересмотр",
        "уточнен",
        "введен",
        "заменен",
        "приведен",
        "рассчитыва",
        "включа",
        "учитыва",
        "осуществля",
    )
    return any(verb in lowered for verb in verbs)


def _extract_years(text: str) -> list[int]:
    """Извлечь все упомянутые в тексте годы, включая границы диапазонов."""
    years: set[int] = {int(match) for match in _YEAR_PATTERN.findall(text)}
    for start, end in _RANGE_PATTERN.findall(text):
        years.update({int(start), int(end)})
    return sorted(years)


def _extract_breaks(text: str, note_kind: str) -> list[NoteBreak]:
    """
    Определить годы, в которых ряд теряет сопоставимость, и вид изменения — по каждой
    части примечания: Росстат склеивает в одно примечание перекройку округов, переход
    на итоги переписи и смену методики, а прерывают они разное.

    «С 2017» и «до 2017» — отметка на 2017; «до 2020 включительно» — на 2021; «за 2010‒2018
    с учётом прежнего состава» — на 2019; «за 2011‒2021 пересчитаны» и «за 2012–2022
    с учётом итогов ВПН-2020» — отметки нет; «за 2013–2018 в соответствии с ОКВЭД2» — на 2013;
    перечень «2000–2018 гг. — …; 2019, 2020 гг. — …» — на начало каждого следующего периода. Часть
    без своих признаков получает вид всего примечания, кроме состава территорий: он не прерывает
    изменения субъектов, и без явного признака в самой части такая отметка скрыла бы разрыв.
    Перемена в составе одного субъекта («до 2006 г. — Пермская область без учета
    Коми-Пермяцкого автономного округа») — отметка только этому субъекту.
    """
    found: dict[tuple[int, str], list[str]] = {}
    periods: list[tuple[int, int, str]] = []
    recalculated = False
    unmarked = _detect_kind(text.lower(), skip=BreakKind.TERRITORY) or BreakKind.METHODOLOGY

    for clause in _CLAUSE_PATTERN.split(text):
        lowered = clause.lower()
        if _RECALCULATED_MARKER in lowered:
            recalculated = True
            continue
        kind = _detect_kind(lowered) or unmarked
        for year, start, end in _clause_break_years(clause):
            for code in _subjects_near(lowered, start, end) or ("",):
                found.setdefault((year, code), []).append(BreakKind.TERRITORY if code else kind)
        periods.extend((first, last, kind) for first, last in _period_spans(clause))

    for year, kind in _period_starts(periods):
        found.setdefault((year, ""), []).append(kind)

    # Без явных формулировок граница — единственный упомянутый год, кроме пересчёта.
    if not found and not recalculated:
        years = _extract_years(text)
        if len(years) == 1:
            found[(years[0], "")] = [note_kind]

    return [
        NoteBreak(year, _prevailing_kind(kinds), code)
        for (year, code), kinds in sorted(found.items())
    ]


def _clause_break_years(clause: str) -> list[tuple[int, int, int]]:
    """
    Годы разрыва по формулировкам одной части — «с», «до», «до … включительно», «с … по …»,
    диапазон лет или дат — и место формулировки в части.
    """
    found: list[tuple[int, int, int]] = []
    inclusive = {int(year) for year in _UNTIL_INCLUSIVE_PATTERN.findall(clause)}
    for match in _SINCE_PATTERN.finditer(clause):
        found.append((int(match.group(1)), *match.span()))
    for match in _UNTIL_PATTERN.finditer(clause):
        year = int(match.group(1))
        found.append((year + 1 if year in inclusive else year, *match.span()))
    for match in _FROM_TO_PATTERN.finditer(clause):
        found.append((int(match.group(2)) + 1, *match.span()))
    plans = set(_PLAN_RANGE_PATTERN.findall(clause))
    lowered = clause.lower()
    for pattern in (_RANGE_PATTERN, _DATE_RANGE_PATTERN):
        for match in pattern.finditer(clause):
            if match.groups() in plans:
                continue
            # О диапазоне говорят слова после него — до следующего года.
            after = _YEAR_PATTERN.search(lowered, match.end())
            tail = lowered[match.end() : after.start() if after else len(lowered)]
            if _CENSUS_BASIS_MARKER in tail:
                continue
            first, last = int(match.group(1)), int(match.group(2))
            year = first if _NEW_CLASSIFIER_PATTERN.search(tail) else last + 1
            found.append((year, *match.span()))
    return found


def _subjects_near(lowered: str, start: int, end: int) -> tuple[str, ...]:
    """
    Субъекты перемены, к которой относится формулировка с годом: по словам после неё
    до запятой или следующего года, а если там их нет — перед ней от прошлой запятой или года.
    """
    stops = [len(lowered)]
    stops += [position for mark in (",", ";") if (position := lowered.find(mark, end)) != -1]
    if (following := _YEAR_PATTERN.search(lowered, end)) is not None:
        stops.append(following.start())
    starts = [0, lowered.rfind(",", 0, start) + 1]
    starts += [match.end() for match in _YEAR_PATTERN.finditer(lowered, 0, start)]
    return _subjects_in(lowered[end : min(stops)]) or _subjects_in(lowered[max(starts) : start])


def _subjects_in(text: str) -> tuple[str, ...]:
    """Субъекты перемен в составе, названных в отрывке."""
    codes: dict[str, None] = {}
    for pattern, found in _SUBJECT_CHANGES:
        if pattern.search(text):
            codes.update(dict.fromkeys(found))
    return tuple(codes)


def _period_spans(clause: str) -> list[tuple[int, int]]:
    """Периоды перечня в части примечания — первый и последний год; годы вразброс не период."""
    spans = []
    for match in _PERIOD_HEAD_PATTERN.finditer(clause):
        parts = []
        for part in match.group(1).split(","):
            years = [int(year) for year in _YEAR_PATTERN.findall(part)]
            parts.append((years[0], years[-1]))
        if all(later[0] == earlier[1] + 1 for earlier, later in itertools.pairwise(parts)):
            spans.append((parts[0][0], parts[-1][1]))
    return spans


def _period_starts(periods: list[tuple[int, int, str]]) -> list[tuple[int, str]]:
    """Начала периодов, продолжающих предыдущий без пропуска лет: смена порядка счёта."""
    ordered = sorted(set(periods))
    return [
        (later[0], later[2])
        for earlier, later in itertools.pairwise(ordered)
        if later[0] == earlier[1] + 1
    ]


def _prevailing_kind(kinds: list[str]) -> str:
    """
    Вид отметки года, названного в нескольких частях: смена правил счёта прерывает
    изменения субъектов и поэтому берёт верх над перекройкой территорий.
    """
    return next((kind for kind in kinds if kind != BreakKind.TERRITORY), kinds[0])


# =======================================================================================
# Направленность показателя
# =======================================================================================

# Показатели-дестимуляторы: рост значения ухудшает положение территории.
_NEGATIVE_MARKERS = (
    "смертност",
    "заболеваемост",
    "безработиц",
    "безработн",
    "преступлен",
    "бедност",
    "малоимущ",
    "износ",
    "выброс",
    "сброс",
    "загрязн",
    "отход",
    "задолженност",
    "просрочен",
    "убыл",
    "убыточн",
    "убыток",
    "аварийн",
    "ветхий",
    "травматизм",
    "несчастн",
    "опасными условиями",
    "инвалидност",
    "дефицит",
    "миграционная убыль",
    "разводимост",
    "соотношение стоимости",
    "без сохранения заработн",
    "в простое",
    "по инициативе работодателя",
)

# Показатели-стимуляторы: рост значения улучшает положение территории.
_POSITIVE_MARKERS = (
    "доход",
    "заработн",
    "ожидаемая продолжительность жизни",
    "ввод в действие",
    "инвестиц",
    "производство",
    "производства",
    "обеспеченност",
    "рождаемост",
    "прибыл",
    "финансовый результат",
    "оборот",
    "экспорт",
    "выпуск",
    "численность студентов",
    "объем услуг",
    "валовой региональный продукт",
    "инновацион",
    "благоустроен",
    "передовые производственные технологии",
)

# Сокращение вреда сильнее дестимуляторов: «доля уловленных загрязняющих веществ».
_MITIGATION_MARKERS = ("уловлен", "улавливан", "обезвреж", "утилизирован")

# Величины, у которых нет направленности: доли в структуре, распределения, цены,
# пороги, долг без просрочки, затраты на охрану природы.
_NEUTRAL_PATTERN = re.compile(
    r"(?:^|: )(?:видов\w* |товарн\w* )?(?:структур|распределени)|индекс\w* цен|"
    r"удельный вес (?:\w+ (?:малых|средних)|индивидуальных|розничных)|"
    r"охран\w* окружающей|природоохранн|дифференциац|децильн|"
    r"дебиторск|кредиторск|задолженност\w* по (?:жилищным |ипотечным жилищным )?кредит"
)

# Виды деятельности по ОКВЭД: «отходов» в них называет отрасль, а не направленность.
_ACTIVITY_PATTERN = re.compile(
    r"водоснабжени\w*|водоотведени\w*|организаци\w* сбора и утилизации отходов|"
    r"деятельность по ликвидации загрязнений|обрабатывающ\w* производств\w*|"
    r"распределени\w* (?:воды|электр\w*|газ\w*)|производство и распределение электроэнергии"
)

# «Прибыль минус убыток» — название сальдо, а не признак убытка.
_BALANCE_PATTERN = re.compile(r"прибыль\s*(?:минус|\(\s*)\s*убыт\w*\)?")


def detect_polarity(indicator_name: object, subsection: object = "") -> str:
    """
    Предположить направленность ряда по формулировке — подсказка для сборки.

    Разрез проверяется раньше показателя: у «…утилизации и размещения отходов» всё решает он.
    """
    name = _polarity_text(indicator_name)
    part = _polarity_text(subsection)

    # Просроченный долг — всегда дестимулятор, даже если долг вообще оценки не имеет.
    overdue = "просрочен" in name or "просрочен" in part
    if not overdue and (_is_neutral(name) or _is_neutral(part)):
        return Polarity.UNKNOWN
    for text in (part, name):
        polarity = _polarity_of(text)
        if polarity != Polarity.UNKNOWN:
            return polarity
    return Polarity.UNKNOWN


def _polarity_text(value: object) -> str:
    """Формулировка без названий видов деятельности и сальдо, в нижнем регистре."""
    text = normalize_text(value).lower()
    text = _ACTIVITY_PATTERN.sub(" ", text)
    return _BALANCE_PATTERN.sub(" ", text)


def _is_neutral(text: str) -> bool:
    """Величина без направленности; численность за границей бедности — не порог."""
    if "границ" in text and "бедност" in text:
        return "ниже" not in text
    return bool(_NEUTRAL_PATTERN.search(text))


def _polarity_of(text: str) -> str:
    """Направленность одной формулировки; при встречных признаках — дестимулятор."""
    if any(marker in text for marker in _MITIGATION_MARKERS):
        return Polarity.POSITIVE
    negative = any(marker in text for marker in _NEGATIVE_MARKERS)
    if negative:
        # «С доходами ниже границы бедности» содержит и «доход», и «бедност».
        return Polarity.NEGATIVE
    if any(marker in text for marker in _POSITIVE_MARKERS):
        return Polarity.POSITIVE
    return Polarity.UNKNOWN


# =======================================================================================
# Идентификаторы
# =======================================================================================


def make_checksum(text: str) -> str:
    """Вычислить устойчивый идентификатор текста (SHA-1 нормализованной строки)."""
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def make_code(prefix: str, value: str) -> str:
    """Построить короткий код справочной записи, устойчивый между пересборками склада."""
    digest = hashlib.sha1(value.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    return f"{prefix}_{digest}"


def make_series_key(indicator_code: str, subsection: object) -> str:
    """Построить ключ ряда ``<код показателя>:<разрез>``: ``00`` или хеш названия разреза."""
    normalized = normalize_text(subsection)
    if not normalized or normalized == SENTINEL_STRING_NOT_APPLICABLE:
        return f"{indicator_code}:00"
    digest = hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"{indicator_code}:{digest}"
