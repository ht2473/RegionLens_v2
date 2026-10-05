"""
Страница показателя по формуле: ввод, проверка по нынешним данным, сохранение со сборкой
и удаление. Таблица — своя; чужая — 404.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin

from . import formula_store, formulas, jobs
from .formula_store import Definition
from .formulas import FormulaError
from .models import DatasetSeries, DatasetVersion
from .pages import DatasetMixin
from .series import own_option_groups

# Пример для подсказки поля.
EXAMPLE = "[ДТП] / [Автомобили] * 1000"


class FormulaView(DatasetMixin, BreadcrumbMixin, TemplateView):
    """Новый показатель по формуле (без кода) или правка существующего."""

    template_name = "userdata/formula.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.code = str(kwargs.get("code") or "")
        return super().dispatch(request, *args, **kwargs)

    @property
    def version(self) -> DatasetVersion:
        version = self.dataset.current_version
        if version is None or version.state != DatasetVersion.State.BUILT:
            raise Http404
        return version

    @property
    def existing(self) -> Definition | None:
        if not self.code:
            return None
        found = next(
            (item for item in formula_store.definitions(self.version) if item.code == self.code),
            None,
        )
        if found is None:
            raise Http404
        return found

    def get_crumbs(self) -> tuple[Crumb, ...]:
        return (
            Crumb(title=_("Свои данные"), url=reverse("userdata:index")),
            Crumb(
                title=self.dataset.title,
                url=reverse("userdata:dataset", args=[self.dataset.public_id]),
            ),
            Crumb(title=_("Показатель по формуле")),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        existing = self.existing
        values: dict[str, Any] = kwargs.get("values") or {}
        if not values and existing is not None:
            titles = formula_store.titles(self.dataset, existing.keys)
            values = {
                "title": existing.title,
                "expression": formulas.display(existing.expression, titles),
                "unit": existing.unit,
                "kind": existing.kind,
                "polarity": existing.polarity,
            }
        values.setdefault("kind", DatasetSeries.Kind.RELATIVE.value)
        values.setdefault("polarity", DatasetSeries.Polarity.NEUTRAL.value)
        context.update(
            page_title=_("Показатель по формуле"),
            dataset=self.dataset,
            existing=existing,
            values=values,
            kinds=DatasetSeries.Kind.choices,
            polarities=DatasetSeries.Polarity.choices,
            insert_groups=_insert_groups(self.request, self.dataset.code),
            functions=formulas.FUNCTIONS,
            example=EXAMPLE,
            max_length=formulas.MAX_LENGTH,
        )
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        action = request.POST.get("action", "check")
        if action == "delete" and self.existing is not None:
            title = self.existing.title
            formula_store.remove(self.dataset, self.code)
            jobs.start(self.version, jobs.BUILD)
            messages.success(request, gettext("Показатель «%(title)s» удалён.") % {"title": title})
            return redirect("userdata:dataset", public_id=self.dataset.public_id)
        values = {
            name: request.POST.get(name, "")
            for name in ("title", "expression", "unit", "kind", "polarity")
        }
        try:
            checked = formula_store.check(self.dataset, values, code=self.code)
        except FormulaError as error:
            return self.render_to_response(self.get_context_data(values=values, error=str(error)))
        if action != "save":
            return self.render_to_response(self.get_context_data(values=values, checked=checked))
        try:
            formula_store.save(self.dataset, checked.definition)
        except FormulaError as error:
            return self.render_to_response(self.get_context_data(values=values, error=str(error)))
        state = jobs.start(self.version, jobs.BUILD)
        if state != jobs.READY:
            return redirect("userdata:series", public_id=self.dataset.public_id)
        key = f"u:{self.dataset.code}:{checked.definition.code}"
        return redirect(f"{reverse('maps:choropleth')}?{urlencode({'series': key})}")


def _insert_groups(request: HttpRequest, code: str) -> list[dict[str, Any]]:
    """
    Свои таблицы для поля «Вставить показатель»: подпись для формулы — название ряда,
    у другой таблицы — с её названием («Таблица: показатель»).
    """
    groups = own_option_groups(request)
    for group in groups:
        for item in group["items"]:
            table = item["search"]
            this = item["key"].startswith(f"u:{code}:")
            item["label"] = item["title"] if this else f"{table}: {item['title']}"
    # Своя таблица — первой.
    groups.sort(key=lambda group: not group["items"][0]["key"].startswith(f"u:{code}:"))
    return groups
