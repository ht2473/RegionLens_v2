"""
Происхождение значений ряда словами: из набора или из выпуска внешнего источника,
предварительные ли они, рассчитаны ли проектом и как ряд сшит с набором.

Тексты собирает сервер; страница показателя, поверхность, паспорт и документы берут их отсюда.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.utils.translation import get_language, gettext

from apps.catalog.constants import ValueFlag
from apps.warehouse.queries.sources import population_last_year, source_links, year_origin


def _method_text(method: str, base_year: int | None) -> str:
    """Как получено годовое значение, если его рассчитал проект."""
    texts = {
        "mean_months": gettext("годовое значение — среднее двенадцати месяцев, расчёт RegionLens"),
        "mean_quarters": gettext("годовое значение — среднее четырёх кварталов, расчёт RegionLens"),
        "real_wage": gettext(
            "рост средней номинальной зарплаты, делённый на рост среднегодовых цен, — расчёт "
            "RegionLens"
        ),
    }
    if method == "per_capita":
        if base_year is None:
            return gettext("итог источника на жителя — расчёт RegionLens")
        return gettext(
            "итог источника на жителя по численности %(year)s года — расчёт RegionLens: "
            "Росстат не публикует численность по субъектам после %(year)s года"
        ) % {"year": base_year}
    return texts.get(method, "")


@dataclass(frozen=True, slots=True)
class SourceLink:
    """Связь ряда с внешним источником и итог сверки с набором."""

    series_key: str
    source_code: str
    method: str
    mode: str  # continue | supersede
    link: str  # full | conditional | revision
    dataset_last_year: int | None
    junction_year: int | None
    last_year: int | None
    publication_ru: str
    publication_en: str
    release: str
    released_on: date | None
    compared_from: int | None
    compared_to: int | None
    pairs: int
    within_share: float | None
    revised_count: int

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> SourceLink:
        """Связь из строки витрины ``mart_source_link``."""
        released = row.get("released_on")
        return cls(
            series_key=row["series_key"],
            source_code=row["source_code"],
            method=row["method"],
            mode=row["mode"],
            link=row["link"],
            dataset_last_year=row.get("dataset_last_year"),
            junction_year=row.get("junction_year"),
            last_year=row.get("last_year"),
            publication_ru=row.get("publication_ru") or "",
            publication_en=row.get("publication_en") or "",
            release=row.get("edition_label") or row.get("release_code") or "",
            released_on=released if isinstance(released, date) else None,
            compared_from=row.get("compared_from"),
            compared_to=row.get("compared_to"),
            pairs=int(row.get("pairs") or 0),
            within_share=row.get("within_share"),
            revised_count=int(row.get("revised_count") or 0),
        )

    @property
    def publication(self) -> str:
        """Название издания источника на языке запроса."""
        if get_language() == "en" and self.publication_en:
            return self.publication_en
        return self.publication_ru

    @property
    def is_conditional(self) -> bool:
        """Сшивка условная: через стык уровни несопоставимы."""
        return self.link == "conditional"

    @property
    def is_revision(self) -> bool:
        """Выпуск источника — новая редакция ряда, а не продолжение."""
        return self.mode == "supersede"

    @property
    def release_text(self) -> str:
        """«выпуск 07-2026 от 02.09.2026»; у файла, различимого датой, — «редакция от …»."""
        if self.released_on is None:
            return gettext("выпуск %(release)s") % {"release": self.release}
        if self.release == self.released_on.isoformat():
            return gettext("редакция от %(date)s") % {"date": self.released_on.strftime("%d.%m.%Y")}
        return gettext("выпуск %(release)s от %(date)s") % {
            "release": self.release,
            "date": self.released_on.strftime("%d.%m.%Y"),
        }

    @property
    def within_percent(self) -> int | None:
        """Доля пар «субъект × год» в допуске, в процентах."""
        return None if self.within_share is None else round(self.within_share * 100)

    @property
    def method_text(self) -> str:
        """Как получено значение, если рассчитано."""
        return _method_text(self.method, self.dataset_last_year)

    @property
    def summary(self) -> str:
        """Одна-две фразы: откуда значения после набора и как ряд сшит."""
        if self.is_revision:
            text = gettext(
                "Значения ряда — из издания Росстата «%(title)s» (%(release)s): оно заменяет "
                "значения набора как более поздняя редакция; уточнения видны в «Пересмотрах»."
            )
            return text % {"title": self.publication, "release": self.release_text}
        if self.junction_year is None:
            return ""
        parts = [
            gettext("Значения с %(year)s года — из издания Росстата «%(title)s» (%(release)s).")
            % {"year": self.junction_year, "title": self.publication, "release": self.release_text}
        ]
        if self.method_text:
            parts.append(self.method_text[:1].upper() + self.method_text[1:] + ".")
        parts.append(self.link_text)
        return " ".join(part for part in parts if part)

    @property
    def link_text(self) -> str:
        """Итог сверки с набором словами."""
        if self.within_percent is None or self.compared_from is None:
            return ""
        values = {
            "first": self.compared_from,
            "last": self.compared_to,
            "share": self.within_percent,
            "year": self.junction_year,
        }
        if self.is_conditional:
            return (
                gettext(
                    "Сшивка условная: за %(first)s–%(last)s годы значения издания совпадают "
                    "с набором в пределах допуска у %(share)s %% пар «регион × год», поэтому "
                    "уровни до и после %(year)s года несопоставимы."
                )
                % values
            )
        return (
            gettext(
                "За %(first)s–%(last)s годы значения издания совпадают с набором в пределах "
                "допуска у %(share)s %% пар «регион × год»."
            )
            % values
        )


@dataclass(frozen=True, slots=True)
class YearOrigin:
    """Происхождение значений ряда за один год."""

    year: int
    link: SourceLink
    flags: ValueFlag
    values: int
    preliminary_values: int

    @property
    def is_preliminary(self) -> bool:
        """Значения года предварительные хотя бы у части регионов."""
        return ValueFlag.PRELIMINARY in self.flags

    @property
    def is_computed(self) -> bool:
        """Значения года рассчитаны проектом."""
        return ValueFlag.COMPUTED in self.flags

    @property
    def source_code(self) -> str:
        """Модуль источника значений года."""
        return self.link.source_code

    @property
    def source_text(self) -> str:
        """Издание и выпуск: «Росстат, «Информация…», выпуск 07-2026 от 02.09.2026»."""
        return f"{source_title(self.link.source_code)}, {self.link.release_text}"

    @property
    def mark(self) -> str:
        """Короткая пометка у значения: «предв.», «расчёт» или «уточн.»."""
        if self.is_preliminary:
            return gettext("предв.")
        if self.is_computed:
            return gettext("расчёт")
        return gettext("уточн.")

    @property
    def text(self) -> str:
        """Пояснение к значениям года."""
        values = {
            "year": self.year,
            "title": self.link.publication,
            "release": self.link.release_text,
        }
        if self.is_preliminary:
            head = gettext(
                "Значения за %(year)s год — предварительные, из издания Росстата «%(title)s» "
                "(%(release)s)."
            )
        else:
            head = gettext(
                "Значения за %(year)s год — из издания Росстата «%(title)s» (%(release)s)."
            )
        parts = [head % values]
        if self.is_computed and self.link.method_text:
            method = self.link.method_text
            parts.append(method[:1].upper() + method[1:] + ".")
        if self.link.is_conditional and self.link.junction_year == self.year:
            parts.append(
                gettext("Сшивка с набором условная: с прошлыми годами уровни несопоставимы.")
            )
        return " ".join(parts)


def release_phrase(label: str, released_on: date | None) -> str:
    """«выпуск 07-2026 от 02.09.2026»; у выпуска, названного датой, — «по состоянию на …»."""
    if released_on is None:
        return gettext("выпуск %(release)s") % {"release": label}
    day = released_on.strftime("%d.%m.%Y")
    if label == released_on.isoformat():
        return gettext("по состоянию на %(date)s") % {"date": day}
    return gettext("выпуск %(release)s от %(date)s") % {"release": label, "date": day}


def source_title(source_code: str) -> str:
    """Издатель и название источника на языке запроса: «Банк России, Сервис получения данных…»."""
    from apps.sources.collect import SOURCES

    module = SOURCES.get(source_code)
    if module is None:
        return source_code
    if get_language() == "en":
        return f"{module.publisher_en}, {module.title_en}"
    return f"{module.publisher_ru}, «{module.title_ru}»"


def source_short(source_code: str) -> str:
    """Короткое имя источника на языке запроса: «бюллетень Росстата», «Банк России»."""
    from apps.sources.collect import SOURCES

    module = SOURCES.get(source_code)
    if module is None:
        return source_code
    return module.short_en if get_language() == "en" else module.short_ru


@dataclass(frozen=True, slots=True)
class CollectedOrigin:
    """Происхождение значений ряда проекта (Банк России, ФНС) за один год."""

    year: int
    source_code: str
    release: str
    released_on: date | None
    flags: ValueFlag
    method: str
    population_year: int | None = None

    @property
    def is_preliminary(self) -> bool:
        """Значения года предварительные хотя бы у части регионов."""
        return ValueFlag.PRELIMINARY in self.flags

    @property
    def is_computed(self) -> bool:
        """Значения года рассчитаны проектом."""
        return ValueFlag.COMPUTED in self.flags

    @property
    def source_text(self) -> str:
        """Источник и выпуск: «Банк России, «Сервис…», по состоянию на 08.09.2026»."""
        return f"{source_title(self.source_code)}, {release_phrase(self.release, self.released_on)}"

    @property
    def mark(self) -> str:
        """Короткая пометка у значения."""
        return gettext("предв.") if self.is_preliminary else gettext("расчёт")

    @property
    def text(self) -> str:
        """Пояснение к значениям года."""
        values = {
            "year": self.year,
            "source": source_title(self.source_code),
            "release": release_phrase(self.release, self.released_on),
        }
        parts = [
            gettext(
                "Значения за %(year)s год — расчёт RegionLens по данным: %(source)s (%(release)s)."
            )
            % values
        ]
        method = {
            "sum": gettext("Годовое значение — сумма двенадцати месяцев."),
            "weighted_mean": gettext(
                "Годовое значение — среднее двенадцати месяцев, взвешенное по объёму выданных "
                "кредитов."
            ),
            "january_next": gettext(
                "Годовое значение — сведения реестра на 10 января следующего года, "
                "как на конец года."
            ),
        }.get(self.method, "")
        if method:
            parts.append(method)
        if ValueFlag.FROZEN_DENOMINATOR in self.flags and self.population_year:
            parts.append(
                gettext(
                    "Численность населения — за %(base)s год: Росстат не публикует её по субъектам "
                    "позже."
                )
                % {"base": self.population_year}
            )
        return " ".join(parts)


type Origin = YearOrigin | CollectedOrigin


def series_link(series_key: str) -> SourceLink | None:
    """Связь ряда с внешним источником; ``None`` — ряд только из набора."""
    row = source_links().get(series_key)
    return SourceLink.from_row(row) if row is not None else None


def origin_of_year(series_key: str, year: int | None) -> Origin | None:
    """Происхождение значений ряда за год; ``None`` — год целиком из набора."""
    if year is None:
        return None
    collected = _collected_origin(series_key, int(year))
    if collected is not None:
        return collected
    link = series_link(series_key)
    if link is None:
        return None
    row = year_origin(series_key, int(year))
    if row is None:
        return None
    return YearOrigin(
        year=int(year),
        link=link,
        flags=ValueFlag(int(row["flags"] or 0)),
        values=int(row["values"]),
        preliminary_values=int(row["preliminary"] or 0),
    )


def _collected_origin(series_key: str, year: int) -> CollectedOrigin | None:
    """Пометка ряда проекта: только у рассчитанных или предварительных значений."""
    from apps.sources.registry import registry

    item = registry().by_key.get(series_key)
    if item is None:
        return None
    row = year_origin(series_key, year)
    if row is None:
        return None
    flags = ValueFlag(int(row["flags"] or 0))
    if not flags & (ValueFlag.COMPUTED | ValueFlag.PRELIMINARY):
        return None
    released = row.get("released_on")
    return CollectedOrigin(
        year=year,
        source_code=row["source_code"],
        release=row.get("edition_label") or "",
        released_on=released if isinstance(released, date) else None,
        flags=flags,
        method=item.annual,
        population_year=population_last_year() if ValueFlag.FROZEN_DENOMINATOR in flags else None,
    )
