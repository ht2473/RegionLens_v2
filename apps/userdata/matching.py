"""
Сопоставление подписей территорий в таблицах пользователей со справочником.

Правила идут от точного к приблизительному; сомнительное не угадывается, а предлагается
кандидатами. Области с автономными округами без уточнения решаются по таблице целиком
(``match_column``): по правилу Росстата это итог вместе с округами.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache, lru_cache
from typing import Any

from django.conf import settings

from apps.sources.territories import ALIASES, fold_lookalikes, normalize, split_glued

# Правила по порядку применения; каждое можно выключить для оценки его вклада.
RULES = (
    "lookalikes",  # латиница в русских словах
    "glued",  # слипшиеся слова
    "abbreviations",  # обл., кр., респ., АО, ФО, номера сносок внутри подписи
    "qualifier",  # уточнение в скобках: «Ямало-Ненецкий автономный округ (Тюменская область)»
    "aliases",  # разговорные формы и сокращения
    "english",  # английские названия
    "codes",  # ISO 3166-2 и ОКАТО
    "short",  # краткая форма без слова вида: «Татарстан»
    "composite",  # составные строки: «Архангельская область и Ненецкий автономный округ»
    "outside",  # территории вне справочника с причиной
    "nested",  # вложенные территории: вопрос вместо молчаливого итога
    "fuzzy",  # нечёткое сравнение с порогом и зазором
)

# Подпись узнана (код известен).
EXACT = "exact"
# Написание исправлено нечётким сравнением.
FIXED = "fixed"
# Область с округами без уточнения: по правилу Росстата — итог, но таблица может считать иначе.
NESTED = "nested"
# Неоднозначно: человек выбирает из кандидатов.
ASK = "ask"
# Территории нет в справочнике сайта.
OUTSIDE = "outside"
# Похоже на район или город.
MUNICIPAL = "municipal"
# Выбор человека, запомненный раньше.
REMEMBERED = "remembered"
# Не территория.
NONE = "none"
# Код страны в справочнике территорий.
COUNTRY_CODE = "RU"

# Принятое нечёткое сравнение: сходство не ниже порога и не ближе зазора ко второму кандидату.
FUZZY_ACCEPT = 0.9
FUZZY_MARGIN = 0.05
# Ниже порога принятия, но выше этого — кандидаты на выбор.
FUZZY_CANDIDATES = 0.75
FUZZY_MIN_LENGTH = 6
# Усечённая подпись принимается, если она не короче этого.
PREFIX_MIN_LENGTH = 12
# Краткая форма короче — не форма, а обрывок.
SHORT_MIN_LENGTH = 3
# Длиннее — уже пояснение под таблицей, а не строка ведомства.
ORGANIZATION_MAX_LENGTH = 80
# Подпись строки страны под названием показателя короче пояснения под таблицей.
COUNTRY_PHRASE_LENGTH = 160

# Области, в которые входят автономные округа: (итог с округами, область без округов).
NESTED_PARENTS = {
    "RU-ARK-AGG": "RU-ARK",
    "RU-TYU-AGG": "RU-TYU",
}
# Автономные округа в составе итога: область без них — итог за вычетом округов.
NESTED_MEMBERS = {
    "RU-ARK-AGG": ("RU-NEN",),
    "RU-TYU-AGG": ("RU-KHM", "RU-YAN"),
}
_NESTED_NAMES = {
    "RU-ARK-AGG": ("архангельская область", "arkhangelsk oblast", "arkhangelsk region"),
    "RU-TYU-AGG": ("тюменская область", "tyumen oblast", "tyumen region"),
}
# Коды ОКАТО и ОКТМО этих областей в таблицах противоречат друг другу: решает название.
_NESTED_CODES = {
    "11000000": "RU-ARK-AGG",
    "11700000": "RU-ARK-AGG",
    "71000000": "RU-TYU-AGG",
    "71600000": "RU-TYU-AGG",
}
_WITHOUT = re.compile(r"\b(без|кроме|excluding|without|except|excl)\b")
_WITH = re.compile(r"\b(с автономн\w*|вместе с|включая|including|incl|with)\b")

_TYPE_WORDS = re.compile(
    r"\b(республика|область|край|автономный округ|автономная область|"
    r"город федерального значения|republic of|republic|oblast|region|province|krai|kray|"
    r"territory|autonomous okrug|autonomous area|autonomous district|autonomous oblast|"
    r"autonomous region|the)\b"
)
_ABBREVIATIONS = (
    (re.compile(r"\bавт\.?\s*обл\b\.?"), "автономная область"),
    (re.compile(r"\bа\.\s*о\b\.?"), "автономный округ"),
    (re.compile(r"\b(\w+ая) ао\b"), r"\1 автономная область"),
    (re.compile(r"\bао\b"), "автономный округ"),
    (re.compile(r"\bресп\b\.?"), "республика"),
    (re.compile(r"^р\.\s*"), "республика "),
    (re.compile(r"\bобл\b\.?"), "область"),
    (re.compile(r"\bкр\b\.?"), "край"),
    (re.compile(r"\bфо\b"), "федеральный округ"),
    (re.compile(r"\bfd\b"), "federal district"),
    (re.compile(r"\bao\b"), "autonomous okrug"),
    # Официальные формы городов (ФТС, Минфин): «Город Москва столица Российской Федерации город
    # федерального значения», «город федерального значения Севастополь».
    (re.compile(r"\s+столица российской федерации\b"), ""),
    (re.compile(r"^(город\s+)?федерального значения\s+(?=\S)"), ""),
    (re.compile(r"[\s—–-]+(город\s+)?федерального значения$"), ""),
    (re.compile(r"^город\s+"), ""),
    (re.compile(r"\s+(г|город)\.?$"), ""),
)
# Номер сноски внутри или в конце подписи: «Сибирский2) федеральный округ», «округ2); 3)».
_INNER_NOTE = re.compile(r"(?<=[^\W\d_])\d{1,2}\)|(?<=\s)\d{1,2}\)")
_ENGLISH_LEAD_IN = re.compile(r"^(including|of which|incl\.?|the)\b\s*:?\s*")
_EDGE_PUNCTUATION = re.compile(r"^[\s;,.:*–-]+|[\s;,.:*–-]+$")
# Сноска под таблицей в столбце подписей: «1) Данные…», «——— 1) По данным…», «*рассчитано…».
_FOOTNOTE = re.compile(
    r"^[\W_]*\d{1,2}\)\s*\S|^\s*\d{1,2}\s*[А-ЯЁA-Z«\"].{30,}|^\s*\*{1,3}\s*\S.{30,}", re.DOTALL
)
# Итоговая строка страны: «Итого по Российской Федерации», «Средний уровень по РФ».
_COUNTRY_TOTAL = re.compile(
    r"(итого|всего|в целом|в среднем|средний уровень|среднее значение)\s+(по\s+)?"
    r"(российской федерации|россии|рф)"
)
# Строка без территории, о которой не спрашивают: номер столбца, сноска, итог без названия.
QUIET_RULES = frozenset({"number", "footnote", "total"})
_BARE_TOTAL = frozenset({"всего", "итого", "total", "в целом", "итого по субъектам"})
# Только слова вида и связки — обрывок разорванной подписи, а не территория.
_GENERIC = re.compile(
    r"\b(республика|область|край|автономн\w*|округ\w*|федеральн\w*|без|кроме|"
    r"в том числе|город|republic|oblast|krai|autonomous|okrug|federal|district|region)\b"
)
_QUALIFIER = re.compile(r"^(?P<name>.+?)\s*\((?P<inner>[^()]*)\)\s*$")
_COMPOSITE = re.compile(r"\s+(?:и|and|\+|&)\s+")
_MUNICIPAL = re.compile(
    r"\b(район\w*|р-н|муниципальн\w*|городской округ|г\.о\.|м\.р\.|поселени\w*|"
    r"посел[её]к\w*|село|станица|municipal\w*|(?<!federal )district)\b|^г\.\s*\S"
)
_ORGANIZATION = re.compile(
    r"(управлени|\bупр\.|агентств|министерств|ведомств|фмба|департамент|\bслужб|"
    r"ministry|agency|department)"
)
_NOT_A_LABEL = re.compile(r"\s*-?(?!\d{8}(?:\d{3})?(?:\.0)?\s*\Z)\d+(?:[.,]\d+)?\s*\Z")
_COUNTRY_PHRASE = re.compile(r"российск\w* федераци|\bпо росси[ия]\b|\bпо рф\b|russian federation")
_DISTRICT_WORDS = re.compile(r"\b(федеральный округ|federal district)\b")
_SOFT_HYPHEN = chr(0xAD)
_ENGLISH_SWAPS = (
    ("Autonomous Okrug", ("Autonomous Area", "Autonomous District", "AO")),
    ("Autonomous Oblast", ("Autonomous Region",)),
    ("Federal District", ("Federal Okrug",)),
    ("Oblast", ("Region", "Province")),
    ("Krai", ("Territory", "Kray")),
)


@dataclass(frozen=True, slots=True)
class Match:
    """Итог сопоставления одной подписи."""

    kind: str
    code: str | None = None
    candidates: tuple[str, ...] = ()
    # У строки вне справочника: merged, new, district, baikonur, abroad, unallocated,
    # federal_territory, composite, organization.
    reason: str = ""
    details: tuple[tuple[str, Any], ...] = ()
    rule: str = ""

    @property
    def is_territory(self) -> bool:
        """Строка относится к территории (узнанной, вне справочника или под вопросом)."""
        return self.kind in {EXACT, FIXED, NESTED, ASK, OUTSIDE, REMEMBERED}

    @property
    def is_resolved(self) -> bool:
        """Код известен без вопроса человеку."""
        return self.code is not None and self.kind in {EXACT, FIXED, REMEMBERED}

    def detail(self, name: str) -> Any:
        """Значение подробности по имени."""
        return dict(self.details).get(name)


@dataclass(frozen=True, slots=True)
class NestedQuestion:
    """Вопрос о вложенной территории: область в таблице с округами или без них."""

    label: str
    total_code: str  # итог с округами — правило Росстата и выбор по умолчанию
    alone_code: str  # область без округов


@dataclass(slots=True)
class ColumnMatch:
    """Сопоставление всех подписей одного столбца таблицы."""

    matches: dict[str, Match]
    nested: list[NestedQuestion] = field(default_factory=list)
    # Код ОКАТО или ОКТМО указывает на другую территорию, чем название: (подпись, код, по коду).
    code_conflicts: list[tuple[str, str, str]] = field(default_factory=list)

    def codes(self) -> dict[str, str]:
        """Подпись → код для узнанных без вопроса."""
        return {
            label: match.code
            for label, match in self.matches.items()
            if match.is_resolved and match.code
        }


class Matcher:
    """Сопоставление по справочнику и перечню написаний; ``disabled`` — выключенные правила."""

    def __init__(self, disabled: Iterable[str] = ()) -> None:
        unknown = set(disabled) - set(RULES)
        if unknown:
            raise ValueError(f"нет правил: {', '.join(sorted(unknown))}")
        self.disabled = frozenset(disabled)
        self.exact: dict[str, tuple[str, str]] = {}
        self.nested: dict[str, str] = {}
        self.codes: dict[str, str] = {}
        self.outside: dict[str, dict[str, Any]] = {}
        self.ambiguous: dict[str, tuple[str, ...]] = {}
        self.short: dict[str, str] = {}
        self.capitals: dict[str, str] = {}
        self.components: dict[frozenset[str], str] = {}
        self.targets: dict[str, str] = {}
        reference = _reference()
        self._index_names(reference)
        self._index_codes(reference)
        self._index_aliases()
        self._index_short(reference)
        self._index_rest(reference)

    def on(self, rule: str) -> bool:
        """Правило включено."""
        return rule not in self.disabled

    # --- Подготовка подписи ------------------------------------------------------------------

    def prepare(self, label: str) -> str:
        """Подпись в виде для сравнения: без сносок, сокращений и случайных знаков."""
        text = label.replace(_SOFT_HYPHEN, "")
        if self.on("lookalikes"):
            text = fold_lookalikes(text)
        if self.on("abbreviations"):
            text = _INNER_NOTE.sub(" ", text)
        text = normalize(text)
        if self.on("glued"):
            text = split_glued(text)
        if self.on("abbreviations"):
            for pattern, replacement in _ABBREVIATIONS:
                text = pattern.sub(replacement, text)
        if self.on("english"):
            text = _ENGLISH_LEAD_IN.sub("", text)
        return _key(_EDGE_PUNCTUATION.sub("", text))

    def key(self, label: str) -> str:
        """Ключ подписи для запомненных выборов человека."""
        return self.prepare(label)

    # --- Сопоставление -----------------------------------------------------------------------

    def match(self, label: str, remembered: Mapping[str, str] | None = None) -> Match:
        """Сопоставить одну подпись; ``remembered`` — запомненные выборы: ключ → код."""
        by_code = self._by_code(label)
        if by_code is not None:
            return by_code
        text = self.prepare(label)
        quiet = _quiet_rule(label, text)
        if quiet is not None or not text or not _GENERIC.sub(" ", text).strip():
            return Match(NONE, rule=quiet or "")
        if remembered and text in remembered:
            if remembered[text] == OUTSIDE:
                return Match(OUTSIDE, reason="remembered", rule="remembered")
            return Match(REMEMBERED, code=remembered[text], rule="remembered")
        return self._known(text) or self._by_rules(label, text)

    def match_column(
        self,
        labels: Iterable[str],
        *,
        codes: Mapping[str, str] | None = None,
        remembered: Mapping[str, str] | None = None,
    ) -> ColumnMatch:
        """
        Сопоставить подписи столбца с правилами таблицы целиком.

        «Архангельская область» без уточнения — итог с округом, если в той же таблице есть
        область без округа; иначе — вопрос. ``codes`` — код ОКАТО или ОКТМО строки: он только
        подтверждает название, расхождение называется в отчёте.
        """
        matches = {label: self.match(label, remembered) for label in dict.fromkeys(labels)}
        column = ColumnMatch(matches=matches)
        present = {found.code for found in matches.values() if found.is_resolved}
        for label, found in matches.items():
            if found.kind != NESTED or found.code is None:
                continue
            alone = NESTED_PARENTS[found.code]
            if alone in present:
                matches[label] = Match(EXACT, code=found.code, rule="nested")
            else:
                column.nested.append(NestedQuestion(label, found.code, alone))
        for label, raw in (codes or {}).items():
            named = matches.get(label)
            by_code = self.match(str(raw))
            if (
                named is not None
                and named.code
                and by_code.kind == EXACT
                and by_code.code not in {None, named.code}
            ):
                column.code_conflicts.append((label, str(raw), by_code.code or ""))
        return column

    # --- Правила ------------------------------------------------------------------------------

    def _known(self, text: str) -> Match | None:  # noqa: PLR0911 — по ветви на перечень
        """Подготовленная подпись есть в одном из перечней."""
        if self.on("nested"):
            parent = self._nested_parent(text)
            if parent is not None:
                return parent
        if text in self.exact:
            code, rule = self.exact[text]
            return Match(EXACT, code=code, rule=rule)
        if self.on("outside") and text in self.outside:
            return self._outside(self.outside[text], rule="outside")
        if text in self.ambiguous:
            return Match(ASK, candidates=self.ambiguous[text], rule="aliases")
        if not self.on("short"):
            return None
        if text in self.short:
            return Match(EXACT, code=self.short[text], rule="short")
        # «Sakha (Yakutia) Republic»: слово вида стоит не там, где в справочнике.
        stripped = _key(_TYPE_WORDS.sub(" ", text))
        if stripped == text:
            return None
        if stripped in self.exact or stripped in self.short:
            code = self.exact[stripped][0] if stripped in self.exact else self.short[stripped]
            return Match(EXACT, code=code, rule="short")
        if stripped in self.ambiguous:
            return Match(ASK, candidates=self.ambiguous[stripped], rule="short")
        return None

    def _by_rules(self, label: str, text: str) -> Match:
        """Правила для подписей, которых нет в перечнях: от уточнения до нечёткого сравнения."""
        found = self._qualified(label) or self._composite(text) or _country_total(text)
        if found is not None:
            return found
        if (
            self.on("outside")
            and len(text) <= ORGANIZATION_MAX_LENGTH
            and _ORGANIZATION.search(text)
        ):
            return Match(OUTSIDE, reason="organization", rule="outside")
        if text in self.capitals:
            return Match(MUNICIPAL, candidates=(self.capitals[text],), rule="municipal")
        # «Валовой региональный продукт по субъектам Российской Федерации» — похоже на страну.
        if (
            _COUNTRY_PHRASE.search(text)
            and len(text) <= COUNTRY_PHRASE_LENGTH
            and label.strip()[:1].isalpha()
        ):
            return Match(ASK, candidates=(COUNTRY_CODE,), rule="country")
        if _MUNICIPAL.search(text) or _MUNICIPAL.search(label.strip().lower()):
            return Match(MUNICIPAL, rule="municipal")
        return self._fuzzy(text) if self.on("fuzzy") else Match(NONE)

    def _nested_parent(self, text: str) -> Match | None:
        """Область с округами: уточнённая — сразу, без уточнения — вопрос по таблице."""
        if text in self.nested:
            return self._nested(self.nested[text], rule="nested")
        for total, names in _NESTED_NAMES.items():
            for name in names:
                if not text.startswith(name + " "):
                    continue
                rest = text[len(name) :]
                if _WITHOUT.search(rest):
                    return Match(EXACT, code=NESTED_PARENTS[total], rule="nested")
                if _WITH.search(rest):
                    return Match(EXACT, code=total, rule="nested")
        return None

    def _qualified(self, label: str) -> Match | None:
        """«Ямало-Ненецкий автономный округ (Тюменская область)»: название до скобок."""
        if not self.on("qualifier"):
            return None
        qualified = _QUALIFIER.match(label.strip())
        if qualified is None:
            return None
        inner = self._known(self.prepare(qualified.group("name")))
        if inner is None or inner.code is None:
            return None
        return Match(inner.kind, code=inner.code, candidates=inner.candidates, rule="qualifier")

    def _composite(self, text: str) -> Match | None:
        """Составная строка: итог, если части — ровно состав итога, иначе вне справочника."""
        if not self.on("composite") or not _COMPOSITE.search(text):
            return None
        codes: set[str] = set()
        for part in _COMPOSITE.split(text):
            found = self._known(_EDGE_PUNCTUATION.sub("", part))
            if found is None or found.code is None:
                return None
            # «Архангельская область и Ненецкий автономный округ»: область здесь — без округа.
            if found.kind == NESTED:
                codes.add(NESTED_PARENTS[found.code])
            else:
                codes.add(found.code)
        whole = self.components.get(frozenset(codes))
        if whole is not None:
            return Match(EXACT, code=whole, rule="composite")
        if not self.on("outside"):
            return None
        parts = (("parts", tuple(sorted(codes))),)
        return Match(OUTSIDE, reason="composite", details=parts, rule="composite")

    def _by_code(self, label: str) -> Match | None:
        """Код ISO 3166-2 или ОКАТО вместо названия; «.0» — след чтения числа из Excel."""
        if not self.on("codes"):
            return None
        raw = re.sub(r"\.0+$", "", label.strip()).upper()
        if raw in self.codes:
            return Match(EXACT, code=self.codes[raw], rule="codes")
        if raw in _NESTED_CODES and self.on("nested"):
            return self._nested(_NESTED_CODES[raw], rule="codes")
        return None

    def _fuzzy(self, text: str) -> Match:
        """Нечёткое сравнение: принять с пометкой, предложить кандидатов или ничего."""
        if len(text) < FUZZY_MIN_LENGTH:
            return Match(NONE)
        # Усечённая подпись «Ямало-Ненецкий автономный» — начало ровно одного названия.
        if len(text) >= PREFIX_MIN_LENGTH:
            heads = {target for key, target in self.targets.items() if key.startswith(text + " ")}
            if len(heads) == 1:
                return self._from_target(heads.pop(), len(text) / (len(text) + 6))
        scored: dict[str, float] = {}
        for key, target in self.targets.items():
            if abs(len(key) - len(text)) > max(4, len(text) // 3):
                continue
            sequence = difflib.SequenceMatcher(None, text, key, autojunk=False)
            if min(sequence.real_quick_ratio(), sequence.quick_ratio()) < FUZZY_CANDIDATES:
                continue
            scored[target] = max(scored.get(target, 0.0), sequence.ratio())
        ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
        if not ranked or ranked[0][1] < FUZZY_CANDIDATES:
            return Match(NONE)
        best_target, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if best >= FUZZY_ACCEPT and best - second >= FUZZY_MARGIN:
            return self._from_target(best_target, best)
        candidates = dict.fromkeys(
            code
            for target, ratio in ranked[:4]
            if ratio >= FUZZY_CANDIDATES
            for code in self._target_codes(target)
        )
        return Match(ASK, candidates=tuple(candidates), rule="fuzzy")

    def _from_target(self, target: str, ratio: float) -> Match:
        kind, _, value = target.partition(":")
        similarity = (("similarity", round(ratio, 3)),)
        if kind == "code":
            return Match(FIXED, code=value, details=similarity, rule="fuzzy")
        if kind == "nested":
            found = self._nested(value, rule="fuzzy")
            return Match(found.kind, found.code, found.candidates, details=similarity, rule="fuzzy")
        found = self._outside(self.outside[value], rule="fuzzy")
        return Match(OUTSIDE, reason=found.reason, details=similarity + found.details, rule="fuzzy")

    @staticmethod
    def _target_codes(target: str) -> tuple[str, ...]:
        kind, _, value = target.partition(":")
        if kind == "code":
            return (value,)
        if kind == "nested":
            return (value, NESTED_PARENTS[value])
        return ()

    @staticmethod
    def _nested(total: str, *, rule: str) -> Match:
        return Match(NESTED, code=total, candidates=(total, NESTED_PARENTS[total]), rule=rule)

    @staticmethod
    def _outside(entry: Mapping[str, Any], *, rule: str) -> Match:
        details = tuple((key, value) for key, value in entry.items() if key != "reason")
        return Match(OUTSIDE, reason=entry["reason"], details=details, rule=rule)

    # --- Перечни ------------------------------------------------------------------------------

    def _wanted(self, name: str) -> bool:
        """Английские написания — только с правилом английских названий."""
        return self.on("english") or not name.isascii()

    def _put(self, name: str, code: str, rule: str) -> None:
        key = self.prepare(name)
        if key and key not in self.nested:
            self.exact.setdefault(key, (code, rule))

    def _index_names(self, reference: dict[str, Any]) -> None:
        """Названия справочника по-русски и по-английски, коды, вложенные области."""
        records = _records(reference)
        for total, names in _NESTED_NAMES.items():
            for name in filter(self._wanted, names):
                if self.on("nested"):
                    self.nested[self.prepare(name)] = total
                else:
                    # Без правила вложенных область без уточнения — итог, как в сборе.
                    self._put(name, total, "reference")
        if self.on("nested") and self.on("codes"):
            self.nested.update(_NESTED_CODES)
        for name, code in ALIASES.items():
            self._put(name, code, "reference")
        for record in records:
            self._put(record["source_name"], record["code"], "reference")
            self._put(record["name_ru"], record["code"], "reference")
        for record in records:
            if self.on("english"):
                for variant in _english_variants(record.get("name_en") or ""):
                    self._put(variant, record["code"], "english")
            # «Кемеровская область — Кузбасс», «Kemerovo Oblast — Kuzbass»: часть до тире.
            for field_name, rule in (("name_ru", "short"), ("name_en", "english")):
                name = record.get(field_name) or ""
                if " — " in name and self._wanted(name):
                    self._put(name.split(" — ")[0], record["code"], rule)

    def _index_codes(self, reference: dict[str, Any]) -> None:
        """Коды справочника и ОКАТО (в восьми и одиннадцати знаках)."""
        for record in _records(reference):
            self.codes[record["code"]] = record["code"]
            okato = record.get("okato")
            if okato and record["code"] != "RU" and okato not in _NESTED_CODES:
                self.codes[okato] = record["code"]
                self.codes[okato + "000"] = record["code"]

    def _index_aliases(self) -> None:
        """Разговорные формы, неоднозначные написания и территории вне справочника."""
        aliases = _aliases()
        for code, names in aliases["aliases"].items():
            for name in names:
                rule = "english" if name.isascii() else "aliases"
                if self.on(rule):
                    self._put(name, code, rule)
        for entry in aliases["ambiguous"]:
            for name in entry["names"]:
                if self.on("english" if name.isascii() else "aliases"):
                    self.ambiguous[self.prepare(name)] = tuple(entry["codes"])
        for entry in aliases["outside"]:
            details = {key: value for key, value in entry.items() if key != "names"}
            for name in filter(self._wanted, entry["names"]):
                self.outside.setdefault(self.prepare(name), details)

    def _index_short(self, reference: dict[str, Any]) -> None:
        """Краткие формы — только единственные; неоднозначные («Алтай») — в перечне вопросов."""
        if not self.on("short"):
            return
        subjects = {record["code"] for record in reference["regions"]}
        short: dict[str, set[str]] = {}
        for key, (code, _rule) in self.exact.items():
            stripped = _key(_TYPE_WORDS.sub(" ", key))
            if code in subjects and stripped != key and len(stripped) >= SHORT_MIN_LENGTH:
                short.setdefault(stripped, set()).add(code)
        # Округ без слов «федеральный округ»: «Дальневосточный 2» в бюллетене.
        for record in reference["federal_districts"]:
            for name in filter(self._wanted, (record["source_name"], record.get("name_en") or "")):
                stripped = _key(_DISTRICT_WORDS.sub(" ", self.prepare(name)))
                if stripped:
                    short.setdefault(stripped, set()).add(record["code"])
        taken = self.exact.keys() | self.ambiguous.keys() | self.nested.keys()
        for stripped, codes in short.items():
            if len(codes) == 1 and stripped not in taken:
                self.short[stripped] = next(iter(codes))

    def _index_rest(self, reference: dict[str, Any]) -> None:
        """Составы итогов, центры субъектов и цели нечёткого сравнения."""
        for record in reference["aggregates"]:
            self.components[frozenset(record["components"])] = record["code"]
        for record in reference["regions"]:
            for field_name in ("capital_ru", "capital_en"):
                key = self.prepare(record.get(field_name) or "")
                if key and key not in self.exact and key not in self.short:
                    self.capitals.setdefault(key, record["code"])
        targets = {
            key: f"code:{code}" for key, (code, rule) in self.exact.items() if rule != "codes"
        }
        targets.update({key: f"code:{code}" for key, code in self.short.items()})
        if self.on("nested"):
            targets.update(
                {key: f"nested:{code}" for key, code in self.nested.items() if not key.isdigit()}
            )
        if self.on("outside"):
            targets.update({key: f"outside:{key}" for key in self.outside})
        self.targets = {
            key: value for key, value in targets.items() if len(key) >= FUZZY_MIN_LENGTH
        }


def _records(reference: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        reference["country"],
        *reference["federal_districts"],
        *reference["regions"],
        *reference["aggregates"],
    ]


def _quiet_rule(label: str, text: str) -> str | None:
    """Строка без территории, о которой не спрашивают: сноска, номер столбца, «Всего»."""
    if _FOOTNOTE.match(label):
        return "footnote"
    # Подготовка снимает и сам номер («1» → «»): проверяется и исходная подпись.
    if re.fullmatch(r"[\d\s.,]+", text or label.strip() or "-"):
        return "number"
    if text in _BARE_TOTAL:
        return "total"
    return None


def _country_total(text: str) -> Match | None:
    """«Итого по Российской Федерации», «Средний уровень по РФ» — строка страны."""
    if _COUNTRY_TOTAL.fullmatch(text):
        return Match(EXACT, code=COUNTRY_CODE, rule="country")
    return None


def _key(text: str) -> str:
    """Дефис и пробел в названиях пишут по-разному: для сравнения они одно и то же."""
    return re.sub(r"[\s,\-]+", " ", text).strip()


def _english_variants(name: str) -> set[str]:
    """Английское название и его распространённые формы: Oblast/Region, Krai/Territory…"""
    variants = {name}
    for source, replacements in _ENGLISH_SWAPS:
        for variant in list(variants):
            if source in variant:
                variants.update(
                    variant.replace(source, replacement) for replacement in replacements
                )
    for variant in list(variants):
        republic_of = re.fullmatch(r"Republic of (.+)", variant)
        if republic_of:
            variants.add(f"{republic_of.group(1)} Republic")
        republic = re.fullmatch(r"(.+) Republic", variant)
        if republic:
            variants.add(f"Republic of {republic.group(1)}")
    return variants


@cache
def _reference() -> dict[str, Any]:
    path = settings.REFERENCE_DIR / "territories.json"
    return json.loads(path.read_bytes().decode("utf-8"))


@cache
def _aliases() -> dict[str, Any]:
    path = settings.REFERENCE_DIR / "territory_aliases.json"
    return json.loads(path.read_bytes().decode("utf-8"))


@cache
def matcher(disabled: frozenset[str] = frozenset()) -> Matcher:
    """Сопоставитель с выключенными правилами; строится один раз на процесс."""
    return Matcher(disabled)


def match(label: str, remembered: Mapping[str, str] | None = None) -> Match:
    """Сопоставить подпись всеми правилами."""
    return matcher().match(label, remembered)


@lru_cache(maxsize=65536)
def quick_match(label: str) -> Match:
    """Сопоставление без нечёткого сравнения: для поиска столбца территорий по всей таблице."""
    return matcher(frozenset({"fuzzy"})).match(label)


def districts_in(values: Iterable[Any]) -> set[str]:
    """Коды федеральных округов среди значений столбца."""
    codes = {record["code"] for record in _reference()["federal_districts"]}
    found = set()
    for value in values:
        if isinstance(value, str) and value.strip() and not _NOT_A_LABEL.match(value):
            result = quick_match(value.strip())
            if result.is_resolved and result.code and result.code in codes:
                found.add(result.code)
    return found


def subjects_in(values: Iterable[Any]) -> set[str]:
    """Коды субъектов среди значений столбца; области с округами — по правилу Росстата."""
    from apps.sources.territories import region_codes

    found = set()
    for value in values:
        # Число — не подпись территории, если это не код ОКАТО.
        if isinstance(value, str) and value.strip() and not _NOT_A_LABEL.match(value):
            result = quick_match(value.strip())
            if result.code and (result.is_resolved or result.kind == NESTED):
                found.add(NESTED_PARENTS.get(result.code, result.code))
    return found & region_codes()


def match_column(
    labels: Iterable[str],
    *,
    codes: Mapping[str, str] | None = None,
    remembered: Mapping[str, str] | None = None,
) -> ColumnMatch:
    """Сопоставить подписи столбца всеми правилами."""
    return matcher().match_column(labels, codes=codes, remembered=remembered)
