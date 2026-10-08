"""
Перечень представлений рабочей поверхности: карта, динамика, рейтинг, распределение, таблица.

У каждого представления свой адрес; переключение вкладки — переход по ссылке.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.compare.panel import build as build_compare
from apps.exports.constants import ReportKind
from apps.maps.panel import build as build_map
from apps.rankings.panel import build as build_ranking
from apps.surface.builders import build_distribution, build_table
from apps.surface.state import SurfaceState, build_query

if TYPE_CHECKING:  # pragma: no cover - только для проверки типов
    from django.utils.functional import _StrOrPromise as Translatable
else:
    # Во время выполнения ленивый перевод приводится к строке при обращении.
    Translatable = str

# Коды представлений — те же, что у сохранённых видов в кабинете.
PANEL_MAP = "map"
PANEL_COMPARE = "compare"
PANEL_RANKINGS = "rankings"
PANEL_DISTRIBUTION = "distribution"
PANEL_TABLE = "table"


@dataclass(frozen=True, slots=True)
class Panel:
    """Одно представление холста."""

    code: str
    url_name: str
    # Имя представления — одно для вкладки, заголовка окна и «хлебных крошек».
    tab: Translatable
    lead: Translatable
    icon: str
    template: str
    # «Настроить вид» над графиком: параметры построения представления.
    view_template: str
    builder: Callable[[HttpRequest, SurfaceState], dict[str, Any]]
    # Параметры построения представления; общее состояние сюда не входит.
    params: tuple[str, ...] = ()
    # Вид отчёта выгрузки: срез по субъектам за год или ряд по годам.
    export_kind: str = ReportKind.RANKING

    @property
    def url(self) -> str:
        """Собственный адрес представления без параметров."""
        return reverse(self.url_name)


PANELS: tuple[Panel, ...] = (
    Panel(
        code=PANEL_MAP,
        url_name="maps:choropleth",
        tab=_("Карта"),
        lead=_(
            "Распределение значений по субъектам Российской Федерации. Способ разбиения "
            "шкалы задаётся явно и указан в легенде: одни и те же данные при равных "
            "интервалах и при квантилях дают разные картины."
        ),
        icon="map",
        template="maps/partials/_map_surface.html",
        view_template="maps/partials/_view.html",
        builder=build_map,
        params=("method", "classes", "mode", "compare"),
    ),
    Panel(
        code=PANEL_COMPARE,
        url_name="compare:index",
        tab=_("Динамика"),
        lead=_(
            "Как менялся показатель у выбранных территорий, чем они отличаются друг "
            "от друга и на каком фоне идут — линии, профиль по ключевым показателям "
            "и таблица различий."
        ),
        icon="dynamics",
        template="compare/partials/_compare_results.html",
        view_template="compare/partials/_view.html",
        builder=build_compare,
        params=("basis", "span"),
        export_kind=ReportKind.SERIES,
    ),
    Panel(
        code=PANEL_RANKINGS,
        url_name="rankings:index",
        tab=_("Рейтинг"),
        lead=_(
            "Позиции субъектов и их изменение год к году. Рядом с местом всегда показано "
            "значение: между соседними позициями может лежать как двукратная разница, "
            "так и доли процента."
        ),
        icon="ranking",
        template="rankings/partials/_ranking_results.html",
        view_template="rankings/partials/_view.html",
        builder=build_ranking,
        params=("order", "district", "base"),
    ),
    Panel(
        code=PANEL_DISTRIBUTION,
        url_name="surface:distribution",
        tab=_("Распределение"),
        lead=_(
            "Как устроен разброс значений: где на шкале стоит каждый регион, где "
            "проходят квартили и в какой части распределения выбранные территории."
        ),
        icon="distribution",
        template="surface/panels/_distribution.html",
        view_template="surface/panels/_view_distribution.html",
        builder=build_distribution,
        params=("method", "classes"),
    ),
    Panel(
        code=PANEL_TABLE,
        url_name="surface:table",
        tab=_("Таблица"),
        lead=_(
            "Все субъекты подряд, включая те, по которым значения нет; в рейтинге — только "
            "участники ранжирования."
        ),
        icon="table",
        template="surface/panels/_table.html",
        view_template="surface/panels/_view_table.html",
        builder=build_table,
        params=("sort", "district"),
        # Выгрузка с пропусками, как и сама таблица.
        export_kind=ReportKind.SERIES,
    ),
)

PANELS_BY_CODE: dict[str, Panel] = {panel.code: panel for panel in PANELS}


def panel_url(
    panel: Panel,
    state: SurfaceState,
    request: HttpRequest,
    *,
    source: Panel | None = None,
    with_territories: bool = True,
) -> str:
    """
    Собрать адрес представления с текущим состоянием и общими параметрами построения.

    ``with_territories=False`` — для ссылки «снять выбор».
    """
    origin = source or panel
    pairs = [
        (name, value) for name, value in state.query if with_territories or name != "territory"
    ]
    carried = [
        (name, value)
        for name in panel.params
        if name in origin.params
        for value in request.GET.getlist(name)
    ]
    query = build_query([*pairs, *carried])
    return f"{panel.url}?{query}" if query else panel.url


def view_query(
    panel: Panel,
    state: SurfaceState,
    request: HttpRequest,
    **overrides: str,
) -> str:
    """
    Собрать строку запроса текущего вида целиком: состояние и все параметры построения.

    Имя из ``overrides`` с пустым значением из адреса убирается.
    """
    pairs = list(state.query)
    for name in panel.params:
        if name in overrides:
            continue
        pairs.extend((name, value) for value in request.GET.getlist(name))
    pairs.extend((name, value) for name, value in overrides.items() if value)
    return build_query(pairs)


def view_url(
    panel: Panel,
    state: SurfaceState,
    request: HttpRequest,
    **overrides: str,
) -> str:
    """Собрать адрес текущего вида с изменёнными параметрами построения."""
    query = view_query(panel, state, request, **overrides)
    return f"{panel.url}?{query}" if query else panel.url


def build_tabs(current: Panel, state: SurfaceState, request: HttpRequest) -> list[dict[str, Any]]:
    """
    Собрать переключатель представлений с общим состоянием в адресах.

    Переносятся и параметры, одинаковые у обоих представлений (шкала карты
    и распределения, округ рейтинга и таблицы), остальные — нет.
    """
    return [
        {
            "code": panel.code,
            "title": panel.tab,
            "icon": panel.icon,
            "url": panel_url(panel, state, request, source=current),
            "active": panel.code == current.code,
        }
        for panel in PANELS
    ]
