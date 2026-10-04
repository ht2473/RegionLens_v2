"""
Параметры сохранённого вида словами — подписями самих страниц.

Неизвестный параметр не показывается. Подписи берутся при вызове, чтобы не загружать
ядро анализа вместе с кабинетом.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from django.utils.translation import gettext

from apps.catalog.indicator import describe_series
from apps.catalog.models import Section, Series, Territory
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.routing import is_user_key

# Сколько названий перечисляется, прежде чем остальные сворачиваются в «ещё N».
LISTED = 3


def describe_parameters(target: str, parameters: dict[str, Any] | None) -> list[str]:
    """Параметры вида по порядку чтения: что, когда, где, как построено."""
    values = parameters or {}
    phrases: list[str] = []

    phrases.extend(_series_phrases(values))
    phrases.extend(_time_phrases(values))
    if territories := _territory_names(_as_list(values.get("territory"))):
        phrases.append(_listed(territories))
    phrases.extend(_build_phrases(target, values))
    return phrases


# ---------------------------------------------------------------------------------------
# Что: показатели
# ---------------------------------------------------------------------------------------


def _series_phrases(values: dict[str, Any]) -> list[str]:
    """Показатели: один, несколько (индекс, корреляции) или пара осей рассеяния."""
    keys = _as_list(values.get("series"))
    pair = [key for key in (values.get("x"), values.get("y")) if key]
    titles = _series_titles([*keys, *pair])

    phrases = []
    named = [titles[key] for key in keys if key in titles]
    if named:
        phrases.append(_listed(named))
    if len(pair) == 2 and all(key in titles for key in pair):  # noqa: PLR2004 — две оси
        phrases.append(f"{titles[pair[0]]} × {titles[pair[1]]}")
    return phrases


def _series_titles(keys: Iterable[str]) -> dict[str, str]:
    """Короткие названия рядов: из основного набора или наименование с разрезом."""
    wanted = set(keys)
    if not wanted:
        return {}
    found = Series.objects.filter(key__in=wanted).select_related("indicator")
    titles = _user_titles(wanted)
    for series in found:
        try:
            titles[series.key] = describe_series(series).short_title
        except WarehouseNotBuiltError:
            # Без склада — формулировка сборника из справочника.
            titles[series.key] = series.full_title
    return titles


def _user_titles(keys: set[str]) -> dict[str, str]:
    """Названия рядов своих таблиц, доступных тому, кто открыл страницу."""
    from apps.userdata.series import user_series

    titles = {}
    for key in keys:
        if is_user_key(key):
            found = user_series(key)
            if found is not None:
                titles[key] = found.full_title
    return titles


# ---------------------------------------------------------------------------------------
# Когда и где
# ---------------------------------------------------------------------------------------


def _time_phrases(values: dict[str, Any]) -> list[str]:
    """Год среза или отрезок лет."""
    phrases = []
    if year := _year(values.get("year")):
        phrases.append(gettext("%(year)s год") % {"year": year})
    first, last = _year(values.get("first")), _year(values.get("last"))
    if first and last:
        phrases.append(f"{first}–{last}")
    return phrases


def _territory_names(codes: list[str]) -> list[str]:
    """Названия субъектов в порядке выбора."""
    if not codes:
        return []
    names = {item.code: item.name for item in Territory.objects.filter(code__in=codes)}
    return [names[code] for code in codes if code in names]


# ---------------------------------------------------------------------------------------
# Как построено
# ---------------------------------------------------------------------------------------


def _build_phrases(target: str, values: dict[str, Any]) -> list[str]:
    """Параметры построения — подписями самих страниц."""
    phrases: list[str] = []
    for name, describe in _BUILDERS:
        phrase = describe(target, values) if name in values else ""
        if phrase:
            phrases.append(phrase)
    # Веса индекса приходят по ключу ряда: «weight:<ключ>».
    if any(name.startswith("weight:") for name in values):
        phrases.append(_weights(target, values))
    return phrases


def _scale(target: str, values: dict[str, Any]) -> str:
    """Шкала карты и распределения; у корреляций «method» — вид коэффициента."""
    if target == "correlation":
        from apps.analytics.core.correlation import METHODS as CORRELATIONS

        label = CORRELATIONS.get(values["method"])
        return gettext("коэффициент %(name)s") % {"name": label} if label else ""

    from apps.maps.classification import METHODS

    label = METHODS.get(values["method"])
    if not label:
        return ""
    classes = values.get("classes")
    if classes and str(classes).isdigit():
        return gettext("шкала — %(method)s, классов: %(count)s") % {
            "method": label,
            "count": classes,
        }
    return gettext("шкала — %(method)s") % {"method": label}


def _mode(_target: str, values: dict[str, Any]) -> str:
    from apps.maps.panel import MODE_TILES

    return gettext("плиточная карта") if values["mode"] == MODE_TILES else ""


def _compare(_target: str, values: dict[str, Any]) -> str:
    year = _year(values["compare"])
    return gettext("изменение к %(year)s году") % {"year": year} if year else ""


def _order(_target: str, values: dict[str, Any]) -> str:
    return gettext("по возрастанию") if values["order"] == "asc" else ""


def _district(_target: str, values: dict[str, Any]) -> str:
    district = Territory.objects.federal_districts().filter(code=values["district"]).first()
    return district.name if district else ""


def _base(_target: str, values: dict[str, Any]) -> str:
    year = _year(values["base"])
    return gettext("места к %(year)s году") % {"year": year} if year else ""


def _basis(_target: str, values: dict[str, Any]) -> str:
    from apps.compare.panel import BASES, BASIS_INDEX

    return str(BASES[BASIS_INDEX]) if values["basis"] == BASIS_INDEX else ""


def _span(_target: str, values: dict[str, Any]) -> str:
    from apps.compare.panel import SPAN_COMPARABLE

    return gettext("сопоставимый отрезок") if values["span"] == SPAN_COMPARABLE else ""


def _sort(_target: str, values: dict[str, Any]) -> str:
    return gettext("строки по значению") if values["sort"] == "value" else ""


def _weighting(target: str, values: dict[str, Any]) -> str:
    if target == "inequality":
        from apps.analytics.views.inequality import WEIGHTING

        return str(WEIGHTING.get(values["weighting"], ""))
    from apps.analytics.core import weighting

    label = weighting.METHODS.get(values["weighting"])
    return gettext("веса — %(name)s") % {"name": label} if label else ""


def _epsilon(_target: str, values: dict[str, Any]) -> str:
    return gettext("неприятие неравенства — %(value)s") % {"value": values["epsilon"]}


def _scheme(_target: str, values: dict[str, Any]) -> str:
    from apps.analytics.core.spatial import SCHEMES

    label = SCHEMES.get(values["scheme"])
    return gettext("соседи — %(name)s") % {"name": label} if label else ""


def _normalization(_target: str, values: dict[str, Any]) -> str:
    from apps.analytics.core.normalization import METHODS

    label = METHODS.get(values["normalization"])
    return gettext("приведение — %(name)s") % {"name": label} if label else ""


def _aggregation(_target: str, values: dict[str, Any]) -> str:
    from apps.analytics.core.aggregation import METHODS

    label = METHODS.get(values["aggregation"])
    return gettext("свёртка — %(name)s") % {"name": label} if label else ""


def _weights(_target: str, _values: dict[str, Any]) -> str:
    return gettext("свои веса")


def _directions(_target: str, _values: dict[str, Any]) -> str:
    return gettext("своя направленность показателей")


def _section(_target: str, values: dict[str, Any]) -> str:
    section = Section.objects.filter(slug=values["section"]).first()
    return section.name if section else ""


# Порядок — порядок чтения: сначала шкала и вид, потом порядок и отбор.
_BUILDERS: tuple[tuple[str, Callable[[str, dict[str, Any]], str]], ...] = (
    ("method", _scale),
    ("mode", _mode),
    ("compare", _compare),
    ("basis", _basis),
    ("span", _span),
    ("order", _order),
    ("base", _base),
    ("district", _district),
    ("sort", _sort),
    ("weighting", _weighting),
    ("epsilon", _epsilon),
    ("scheme", _scheme),
    ("normalization", _normalization),
    ("aggregation", _aggregation),
    ("direction", _directions),
    ("section", _section),
)


# ---------------------------------------------------------------------------------------
# Разбор значений
# ---------------------------------------------------------------------------------------


def _as_list(value: Any) -> list[str]:
    """Значение параметра списком: одиночное и повторённое хранятся по-разному."""
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _year(value: Any) -> str:
    """Год четырьмя цифрами или пусто."""
    text = str(value or "").strip()
    return text if len(text) == 4 and text.isdigit() else ""  # noqa: PLR2004 — год


def _listed(names: list[str]) -> str:
    """Первые названия и «ещё N», если их больше."""
    if len(names) <= LISTED:
        return ", ".join(names)
    rest = len(names) - LISTED
    return gettext("%(names)s и ещё %(count)s") % {
        "names": ", ".join(names[:LISTED]),
        "count": rest,
    }
