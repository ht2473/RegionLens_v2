"""
Страница показателя по формуле: шаблон («A на 1 000 B», «A − B», «A / B», «доля A в B»)
или своя формула, показатели перетаскиваются в неё; предпросмотр по нынешним данным —
малая карта и «посчитано для N из 85»; сохранение со сборкой и удаление. Таблица — своя;
чужая — 404. Открытая из исследования, сохранённая формула встаёт в него картой.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.db.models import F
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.core.navigation import Crumb
from apps.core.templatetags.formatting import ru_number
from apps.core.throttle import allow, allow_key
from apps.core.views import BreadcrumbMixin

from . import formula_store, formulas, jobs, studies
from .formula_store import Definition
from .formulas import FormulaError
from .models import DatasetSeries, DatasetVersion, Study
from .pages import DatasetMixin
from .series import own_option_groups

# Пример для подсказки поля.
EXAMPLE = "[ДТП] / [Автомобили] * 1000"
FREE = "free"
# Шаблоны: формула с ключами A и B, название и единица по умолчанию.
TEMPLATES: dict[str, tuple[str, Any, Any]] = {
    "per1000": ("{a} / {b} * 1000", _("%(a)s на 1 000 (%(b)s)"), _("на 1 000")),
    "diff": ("{a} - {b}", _("%(a)s − %(b)s"), ""),
    "ratio": ("{a} / {b}", _("%(a)s к %(b)s"), ""),
    "share": ("{a} / {b} * 100", _("Доля «%(a)s» в «%(b)s»"), "%"),
}
TEMPLATE_CHOICES = (
    ("per1000", _("A на 1 000 B")),
    ("diff", _("A − B")),
    ("ratio", _("A / B")),
    ("share", _("Доля A в B, %")),
    (FREE, _("Своя формула")),
)
# Шаблон в записи формулы с ключами: «{A} / {B} * 1000» и т. п.
_KEY = r"\{([A-Za-z0-9_:.\-]+)\}"
_PATTERNS = {
    "per1000": re.compile(rf"^{_KEY}\s*/\s*{_KEY}\s*\*\s*1000$"),
    "diff": re.compile(rf"^{_KEY}\s*-\s*{_KEY}$"),
    "ratio": re.compile(rf"^{_KEY}\s*/\s*{_KEY}$"),
    "share": re.compile(rf"^{_KEY}\s*/\s*{_KEY}\s*\*\s*100$"),
}
# Окно предела проверок предпросмотра, секунд.
PREVIEW_WINDOW = 600


def template_of(expression: str) -> tuple[str, str, str]:
    """Шаблон сохранённой формулы и её ряды A и B; иначе — своя формула."""
    for code, pattern in _PATTERNS.items():
        found = pattern.match(expression.strip())
        if found:
            return code, found.group(1), found.group(2)
    return FREE, "", ""


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
            template, first, second = template_of(existing.expression)
            values = {
                "title": existing.title,
                "expression": formulas.display(existing.expression, titles),
                "unit": existing.unit,
                "kind": existing.kind,
                "polarity": existing.polarity,
                "template": template,
                "a": first,
                "b": second,
            }
        values.setdefault("kind", DatasetSeries.Kind.RELATIVE.value)
        values.setdefault("polarity", DatasetSeries.Polarity.NEUTRAL.value)
        if values.get("template") not in {code for code, _title in TEMPLATE_CHOICES}:
            # Новая формула — шаблоном; присланная без шаблона — своей формулой.
            values["template"] = (
                "per1000" if existing is None and not values.get("expression") else FREE
            )
        groups = _insert_groups(self.request, self.dataset.code)
        study = _study(self.request, self.request.GET.get("study") or values.get("study", ""))
        context.update(
            page_title=_("Показатель по формуле"),
            dataset=self.dataset,
            existing=existing,
            values=values,
            kinds=DatasetSeries.Kind.choices,
            polarities=DatasetSeries.Polarity.choices,
            insert_groups=groups,
            extra_options=_extra_options(self.dataset, groups, values),
            templates=TEMPLATE_CHOICES,
            slots=[("a", "A", values.get("a", "")), ("b", "B", values.get("b", ""))],
            functions=formulas.FUNCTIONS,
            example=EXAMPLE,
            max_length=formulas.MAX_LENGTH,
            study=study,
        )
        checked = kwargs.get("checked")
        if checked is not None:
            context["preview"] = preview(checked)
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        action = request.POST.get("action", "check")
        if action == "delete" and self.existing is not None:
            return self.delete(request)
        values = {
            name: request.POST.get(name, "")
            for name in ("title", "expression", "unit", "kind", "polarity", "template", "a", "b")
        }
        values["study"] = request.POST.get("study", "")
        if request.headers.get("HX-Request") and not _preview_allowed(request):
            return HttpResponse(status=429)
        try:
            checked = formula_store.check(self.dataset, self.from_template(values), code=self.code)
            if action == "save":
                return self.save(checked, values["study"])
        except FormulaError as error:
            return self.render_to_response(self.get_context_data(values=values, error=str(error)))
        return self.render_to_response(self.get_context_data(values=values, checked=checked))

    def delete(self, request: HttpRequest) -> HttpResponse:
        """Убрать формулу и пересобрать таблицу."""
        assert self.existing is not None
        title = self.existing.title
        formula_store.remove(self.dataset, self.code)
        jobs.start(self.version, jobs.BUILD)
        messages.success(request, gettext("Показатель «%(title)s» удалён.") % {"title": title})
        return redirect("userdata:dataset", public_id=self.dataset.public_id)

    def save(self, checked: formula_store.Check, study_id: str) -> HttpResponse:
        """Сохранить формулу и собрать таблицу; ``FormulaError`` — предел формул."""
        formula_store.save(self.dataset, checked.definition)
        if jobs.start(self.version, jobs.BUILD) == jobs.FAILED:
            return redirect("userdata:series", public_id=self.dataset.public_id)
        key = f"u:{self.dataset.code}:{checked.definition.code}"
        return redirect(self.after_save(study_id, key, checked.definition.code))

    def from_template(self, values: dict[str, str]) -> dict[str, str]:
        """
        Данные формулы для проверки: у шаблона — формула из рядов A и B, название
        и единица по умолчанию, если их не задали; у своей формулы — как введено.
        """
        template = values.get("template") or FREE
        if template not in TEMPLATES:
            return values
        first, second = values.get("a", ""), values.get("b", "")
        if not first or not second:
            raise FormulaError(gettext("Выберите оба показателя: A и B."))
        if first == second:
            raise FormulaError(gettext("A и B — один и тот же показатель."))
        expression, title, unit = TEMPLATES[template]
        data = dict(values)
        data["expression"] = expression.format(a="{" + first + "}", b="{" + second + "}")
        if not values.get("title", "").strip():
            titles = formula_store.titles(self.dataset, [first, second])
            data["title"] = str(title) % {
                "a": titles.get(first, first),
                "b": titles.get(second, second),
            }
            values["title"] = data["title"]
        if not values.get("unit", "").strip() and unit:
            data["unit"] = values["unit"] = str(unit)
        return data

    def after_save(self, study_id: str, key: str, code: str) -> str:
        """
        Куда после сохранения: в исследование, откуда открыта формула, — с картой нового
        ряда; иначе на страницу таблицы с выбранным показателем.
        """
        study = _study(self.request, study_id)
        if study is not None:
            try:
                block = studies.add_view(study, "map", urlencode({"series": key}))
            except studies.StudyError:
                block = None
            url = reverse("userdata:study", args=[study.public_id])
            if block is None:
                return f"{url}?{urlencode({'series': key})}"
            query = urlencode({"series": key, "added": block["id"]})
            return f"{url}?{query}#block-{block['id']}"
        url = reverse("userdata:dataset", args=[self.dataset.public_id])
        return f"{url}?{urlencode({'show': code})}#dataset-detail"


def _study(request: HttpRequest, public_id: Any) -> Study | None:
    """Своё исследование по опознавателю из адреса или формы; чужое и неверное — нет."""
    try:
        identifier = UUID(str(public_id))
    except ValueError:
        return None
    return studies.owned(request).filter(public_id=identifier).first()


def _preview_allowed(request: HttpRequest) -> bool:
    """Проверки предпросмотра в пределах частоты: с учётной записи, у гостя — с адреса."""
    limit = settings.USERDATA_FORMULA_PREVIEWS
    if request.user.is_authenticated:
        return allow_key(
            "formula-preview", f"user:{request.user.pk}", limit=limit, window=PREVIEW_WINDOW
        )
    return allow(request, "formula-preview", limit=limit, window=PREVIEW_WINDOW)


def preview(checked: formula_store.Check, prefix: str = "fp") -> dict[str, Any]:
    """
    Предпросмотр формулы: малая карта года, в котором больше всего субъектов со значением,
    и сколько субъектов посчитано из всех.
    """
    from apps.catalog.models import Territory
    from apps.maps.cartogram import build_map
    from apps.maps.classification import classify, sequential_palette

    flags = formula_store.territory_flags()
    by_year: dict[int, dict[str, float]] = {}
    for code, year, value in checked.outcome.rows:
        if flags.get(code) and value is not None:
            by_year.setdefault(int(year), {})[code] = value
    regions = list(Territory.objects.comparable().only("code", "name_ru", "name_en"))
    if not by_year:
        return {"count": 0, "total": len(regions)}
    year = max(by_year, key=lambda item: (len(by_year[item]), item))
    values = by_year[year]
    classification = classify(list(values.values()))
    palette = sequential_palette(classification.class_count) if classification else []
    rows = []
    for territory in regions:
        found = values.get(territory.code)
        index = classification.class_of(found) if classification and found is not None else None
        rows.append(
            {
                "code": territory.code,
                "name": territory.name,
                "title": f"{territory.name} — {ru_number(found)}"
                if found is not None
                else f"{territory.name} — {gettext('нет данных')}",
                "colour": palette[index] if index is not None else "",
                "class_index": index,
            }
        )
    return {
        "year": year,
        "count": len(values),
        "total": len(regions),
        "geo_map": build_map(rows, prefix=prefix),
        "legend": [
            {
                "step": palette[index].rsplit("-", 1)[1],
                "lower": ru_number(item["lower"]),
                "upper": ru_number(item["upper"]),
            }
            for index, item in enumerate(classification.intervals() if classification else [])
        ],
    }


def _insert_groups(request: HttpRequest, code: str) -> list[dict[str, Any]]:
    """
    Свои таблицы для перечня показателей: подпись для формулы — название ряда, у другой
    таблицы — с её названием («Таблица: показатель»). Ряды без значений не предлагаются.
    """
    groups = own_option_groups(request)
    codes = {
        item["key"].removeprefix("u:").split(":", 1)[0]
        for group in groups
        for item in group["items"]
    }
    empty = set(
        DatasetSeries.objects.filter(
            values_count=0,
            version__dataset__code__in=list(codes),
            version__dataset__current_version=F("version"),
        ).values_list("version__dataset__code", "code")
    )
    for group in groups:
        kept = []
        for item in group["items"]:
            dataset_code, series_code = item["key"].removeprefix("u:").split(":", 1)
            if (dataset_code, series_code) in empty:
                continue
            table = item["search"]
            this = dataset_code == code
            item["label"] = item["title"] if this else f"{table}: {item['title']}"
            item["this"] = this
            kept.append(item)
        group["items"] = kept
    groups = [group for group in groups if group["items"]]
    # Своя таблица — первой.
    groups.sort(key=lambda group: not group["items"][0]["this"])
    return groups


def _extra_options(
    dataset: Any, groups: list[dict[str, Any]], values: dict[str, Any]
) -> list[dict[str, str]]:
    """Ряды A и B не из своих таблиц (официальные): их нет в перечне, но выбор показан."""
    known = {item["key"] for group in groups for item in group["items"]}
    wanted = [values.get(name, "") for name in ("a", "b")]
    missing = [key for key in wanted if key and key not in known]
    if not missing:
        return []
    titles = formula_store.titles(dataset, missing)
    return [{"key": key, "label": titles.get(key, key)} for key in dict.fromkeys(missing)]
