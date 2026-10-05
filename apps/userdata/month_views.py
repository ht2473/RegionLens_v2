"""
Вид «По месяцам» своей таблицы: ход показателя по месяцам за последние годы для выбранной
территории и карта последнего периода, за который есть значения большинства субъектов.
"""

from __future__ import annotations

from typing import Any

from django.http import Http404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.catalog.models import Territory
from apps.catalog.monthly import month_map
from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.warehouse.queries import COUNTRY_CODE
from apps.warehouse.queries.monthly import month_latest, month_timeline

from . import monthly
from .models import DatasetVersion
from .pages import ReadableDatasetMixin
from .series import source_line


class MonthsView(ReadableDatasetMixin, BreadcrumbMixin, TemplateView):
    """Ход по месяцам и карта последнего периода для группы рядов таблицы."""

    template_name = "userdata/months.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        return (
            Crumb(title=_("Свои данные"), url=reverse("userdata:index")),
            Crumb(
                title=self.dataset.title,
                url=reverse("userdata:dataset", args=[self.dataset.public_id]),
            ),
            Crumb(title=_("По месяцам")),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        version = self.dataset.current_version
        if version is None or version.state != DatasetVersion.State.BUILT:
            raise Http404
        found = monthly.groups(version)
        if not found:
            raise Http404
        wanted = self.request.GET.get("group", "")
        group = next((item for item in found if item.code == wanted), found[0])
        territories = _territories()
        latest = month_latest(group.key, group.value_kind)
        # По умолчанию — Россия, если она есть в таблице, иначе первый субъект со значением.
        country = month_timeline(group.key, COUNTRY_CODE, group.value_kind)
        default = COUNTRY_CODE if any(row["value"] is not None for row in country) else ""
        if not default and latest is not None:
            default = next(
                (row["territory_code"] for row in latest["rows"] if row["value"] is not None), ""
            )
        territory = self.request.GET.get("territory") or default
        if territory not in territories:
            territory = default
        rows = month_timeline(group.key, territory, group.value_kind) if territory else []
        context.update(
            page_title=_("По месяцам"),
            dataset=self.dataset,
            groups=found,
            group=group,
            territories=territories,
            territory=territory,
            territory_name=territories.get(territory, ""),
            chart=monthly.chart(group, rows),
            current=_current(group, rows),
            source=source_line(self.dataset),
        )
        if latest is not None:
            drawn = month_map(monthly.spec(group), latest)
            drawn["period"] = monthly.period_label(group, latest["year"], latest["month"])
            context["map"] = drawn
        return context


def _territories() -> dict[str, str]:
    """Территории для выбора: Россия, затем субъекты по алфавиту."""
    found = {COUNTRY_CODE: str(_("Россия"))}
    regions = sorted(
        ((territory.code, territory.name) for territory in Territory.objects.comparable()),
        key=lambda item: item[1],
    )
    found.update(regions)
    return found


def _current(group: monthly.Group, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Последнее значение территории и изменение к тому же периоду прошлого года."""
    filled = [row for row in rows if row["value"] is not None]
    if not filled:
        return None
    last = filled[-1]
    year, month = int(last["year"]), int(last["month"])
    before = next(
        (
            row["value"]
            for row in filled
            if int(row["year"]) == year - 1 and int(row["month"]) == month
        ),
        None,
    )
    return {
        "period": monthly.period_label(group, year, month),
        "value": last["value"],
        "change": monthly.change(group, last["value"], before),
    }
