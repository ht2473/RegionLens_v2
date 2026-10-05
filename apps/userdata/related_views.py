"""Страница «С чем связан показатель» для ряда своей таблицы."""

from __future__ import annotations

from typing import Any

from django.http import Http404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin

from . import related
from .models import DatasetVersion
from .pages import ReadableDatasetMixin
from .series import FEW_REGIONS, series_of


class RelatedView(ReadableDatasetMixin, BreadcrumbMixin, TemplateView):
    """Связи выбранного ряда таблицы с рядами основного набора."""

    template_name = "userdata/related.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        return (
            Crumb(title=_("Свои данные"), url=reverse("userdata:index")),
            Crumb(
                title=self.dataset.title,
                url=reverse("userdata:dataset", args=[self.dataset.public_id]),
            ),
            Crumb(title=_("С чем связан показатель")),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        version = self.dataset.current_version
        if version is None or version.state != DatasetVersion.State.BUILT:
            raise Http404
        choices = [item for item in series_of(version) if item.record.values_count]
        if not choices:
            raise Http404
        wanted = self.request.GET.get("series", "")
        series = next((item for item in choices if item.key == wanted), choices[0])
        context.update(
            page_title=_("С чем связан показатель"),
            dataset=self.dataset,
            choices=choices,
            series=series,
            too_few=series.record.regions_count < FEW_REGIONS,
            per_capita=series.per_capita(),
        )
        if not context["too_few"]:
            context["links"] = related.related(series)
        return context
