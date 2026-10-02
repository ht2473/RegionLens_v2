"""
Общее состояние рабочей поверхности: показатель, территории и год.

Непонятный параметр заменяется значением по умолчанию, как в :mod:`apps.catalog.selectors`.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from django.http import HttpRequest
from django.utils.translation import gettext

from apps.catalog.models import Series, Territory
from apps.catalog.selectors import (
    default_year,
    resolve_series,
    resolve_territories,
    resolve_year,
)
from apps.warehouse.queries import year_counts


@dataclass(frozen=True, slots=True)
class SurfaceState:
    """Выбор, общий для всех представлений холста; годы — по наличию значений у ряда."""

    series: Series | None
    years: list[int]
    year: int | None
    territories: list[Territory]

    @property
    def codes(self) -> list[str]:
        """Коды выбранных территорий в порядке выбора."""
        return [territory.code for territory in self.territories]

    @property
    def query(self) -> list[tuple[str, str]]:
        """Общее состояние в виде пар «имя — значение» для сборки адреса."""
        pairs: list[tuple[str, str]] = []
        if self.series is not None:
            pairs.append(("series", self.series.key))
        if self.year is not None:
            pairs.append(("year", str(self.year)))
        pairs.extend(("territory", code) for code in self.codes)
        return pairs


def resolve_state(request: HttpRequest) -> SurfaceState:
    """
    Разобрать общее состояние из параметров запроса; территории по умолчанию не выбраны.

    Год по умолчанию — последний полный: неполный последний год упорядочил бы горстку субъектов.
    """
    series = resolve_series(request.GET.get("series"))
    counts = year_counts(series.key) if series is not None else {}
    years = sorted(counts)
    year = resolve_year(request.GET.get("year"), years, default=default_year(counts))
    territories = resolve_territories(request.GET.getlist("territory"))
    return SurfaceState(series=series, years=years, year=year, territories=territories)


def dock_summary(state: SurfaceState) -> str:
    """
    Сводка выбора для полосы вызова рейля на телефоне: год и число субъектов.

    Число — после двоеточия: согласование потребовало бы трёх форм склонения.
    """
    parts: list[str] = []
    if state.year is not None:
        parts.append(str(state.year))
    if state.territories:
        parts.append(gettext("субъектов: %(count)d") % {"count": len(state.territories)})
    else:
        parts.append(gettext("все субъекты"))
    return " · ".join(parts)


def state_context(state: SurfaceState) -> dict[str, object]:
    """Разложить состояние по именам, которыми пользуются шаблоны."""
    return {
        "series": state.series,
        "years": state.years,
        "year": state.year,
        "territories": state.territories,
        "selected_codes": state.codes,
        "dock_title": (
            state.series.full_title if state.series is not None else gettext("Ряд не выбран")
        ),
        "dock_summary": dock_summary(state),
    }


def build_query(pairs: list[tuple[str, str]]) -> str:
    """Собрать строку запроса, оставив пустые значения за бортом."""
    filled = [(name, value) for name, value in pairs if value != ""]
    return urlencode(filled) if filled else ""
