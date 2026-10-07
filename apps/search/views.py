"""Страница результатов поиска и подсказки быстрого перехода."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.search import answers, parse, results
from apps.search.constants import EXAMPLES, KIND_LABELS
from apps.search.models import MAX_QUERY_LENGTH, SearchMiss
from apps.search.places import Place

# Наименьшая длина запроса.
MIN_QUERY_LENGTH = 2
# Сколько подсказок каждой группы в быстром переходе.
SUGGEST_LIMIT = 4


def _query(request: HttpRequest) -> str:
    """Текст запроса в допустимой длине."""
    return request.GET.get("q", "").strip()[:MAX_QUERY_LENGTH]


def _home_code(request: HttpRequest) -> str | None:
    """Код моего региона."""
    from apps.accounts.region import my_region

    region = my_region(request)
    return region.code if region else None


def refine(reading: parse.Reading, params: QueryDict) -> parse.Reading:
    """
    Применить уточнения из чипов «понято как»: показатель, места, год, вид ответа.

    Пустое значение параметра — уточнение «без»: ``region=`` снимает места, ``year=`` — год.
    """
    from apps.catalog.models import Territory
    from apps.warehouse.queries import featured_set

    changes: dict[str, Any] = {}
    if "series" in params:
        known = featured_set().by_key()
        changes["series"] = tuple(key for key in params.getlist("series") if key in known)
    if "region" in params:
        wanted = [code for code in params.getlist("region") if code]
        valid = set(Territory.objects.filter(code__in=wanted).values_list("code", flat=True))
        changes["places"] = tuple(Place((code,), -1, -1) for code in wanted if code in valid)
    if "year" in params:
        year = params.get("year", "")
        changes["year"] = int(year) if year.isdigit() else None
    if not changes and "kind" not in params:
        return reading
    refined = replace(reading, **changes)
    kind = params.get("kind", "")
    if kind in KIND_LABELS:
        return replace(refined, kind=kind)
    return replace(refined, kind=parse.reconsider(refined))


def _link(params: QueryDict, **changes: Any) -> str:
    """Адрес страницы результатов с изменёнными параметрами; ``None`` — параметр снять."""
    current = params.copy()
    for key, value in changes.items():
        current.pop(key, None)
        if value is None:
            continue
        current.setlist(key, value if isinstance(value, list) else [value])
    return reverse("search:results") + "?" + current.urlencode()


def chips(reading: parse.Reading, params: QueryDict, names: dict[str, str]) -> list[dict[str, Any]]:
    """Строка «понято как»: вид ответа, показатели, места, год — с заменой и снятием."""
    from apps.warehouse.queries import featured_set

    by_key = featured_set().by_key()
    found: list[dict[str, Any]] = []
    if reading.kind in KIND_LABELS:
        options = []
        if reading.series and reading.kind in (parse.RANK, parse.VALUE, parse.TREND):
            for kind in (parse.RANK, parse.VALUE, parse.TREND):
                if kind == reading.kind or (kind == parse.VALUE and not reading.places):
                    continue
                options.append({"label": KIND_LABELS[kind], "href": _link(params, kind=kind)})
        found.append({"role": "kind", "label": KIND_LABELS[reading.kind], "options": options})
    for key in reading.series:
        item = by_key.get(key)
        if item is None:
            continue
        others = [alt for alt in reading.alternatives if alt not in reading.series][:2]
        found.append(
            {
                "role": "series",
                "label": item.short_title,
                "options": [
                    {
                        "label": by_key[alt].short_title,
                        "href": _link(
                            params, series=[alt if k == key else k for k in reading.series]
                        ),
                    }
                    for alt in others
                    if alt in by_key
                ],
            }
        )
    codes = reading.codes
    for code in codes:
        found.append(
            {
                "role": "place",
                "label": names.get(code, code),
                "remove": _link(params, region=[c for c in codes if c != code] or [""]),
            }
        )
    if reading.year:
        found.append({"role": "year", "label": str(reading.year), "remove": _link(params, year="")})
    return found


class SearchView(BreadcrumbMixin, TemplateView):
    """Страница результатов: ответ, показатели, регионы, термины, страницы."""

    template_name = "search/results.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Поиск")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Разобрать запрос, собрать ответ и группы результатов."""
        from apps.catalog.models import Territory

        context = super().get_context_data(**kwargs)
        query = _query(self.request)
        context["page_title"] = _("Поиск")
        context["query"] = query
        if len(query) < MIN_QUERY_LENGTH:
            context["examples"] = EXAMPLES
            return context
        reading = refine(parse.parse(query), self.request.GET)
        answer = answers.build(reading, _home_code(self.request))
        groups = results.collect(reading)
        codes = [code for place in reading.places for code in place.codes]
        names = {row.code: row.name for row in Territory.objects.filter(code__in=codes)}
        context.update(
            reading=reading,
            answer=answer,
            groups=groups,
            chips=chips(reading, self.request.GET, names) if answer else [],
            found=answer is not None or any(groups.values()),
        )
        if answer is None and "series" not in self.request.GET:
            SearchMiss.record(query, get_language() or "")
        return context


def suggest(request: HttpRequest) -> HttpResponse:
    """Подсказки быстрого перехода по мере ввода: первой — страница результатов."""
    query = _query(request)
    context: dict[str, Any] = {"query": query}
    if len(query) >= MIN_QUERY_LENGTH:
        reading = parse.parse(query)
        groups = results.collect(reading)
        context.update(
            reading=reading,
            kind_label=KIND_LABELS.get(reading.kind, ""),
            groups={name: rows[:SUGGEST_LIMIT] for name, rows in groups.items()},
            results_url=reverse("search:results") + "?" + urlencode({"q": query}),
        )
    return render(request, "search/_suggest.html", context)
