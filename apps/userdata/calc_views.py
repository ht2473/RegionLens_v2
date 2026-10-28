"""
Панель расчёта на поле исследования: расчёт из каталога (`operations.py`) или своя формула.

Ряды — из своих таблиц и официальные ряды поля; предпросмотр по нынешним данным — малая
карта года с наибольшим числом субъектов, крайние регионы, предупреждения о смысле и формула.
Сохранение — новый ряд в выбранной своей таблице (формула) или пересчёт сборки, и карта
нового ряда на поле. Со сценарием панель — окно на странице исследования, без него —
отдельная страница с той же формой.
"""

from __future__ import annotations

from statistics import median
from typing import Any
from urllib.parse import urlencode

from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.http.response import HttpResponseBase
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.core.templatetags.formatting import ru_number
from apps.warehouse import routing

from . import access, formula_store, formulas, jobs, lab, operations, semantics, studies
from .formula_views import preview, preview_allowed
from .formulas import FormulaError
from .models import Dataset, DatasetSeries, DatasetVersion, Study
from .series import own_option_groups
from .study_views import edit_allowed

FREE = "free"
# Поля формы панели: операция, ряды, годы, выбор, таблица, название и единица.
TEXT_FIELDS = (
    "op",
    "a",
    "b",
    "year",
    "first",
    "last",
    "mode",
    "group",
    "table",
    "title",
    "unit",
    "kind",
    "expression",
)
# Крайних регионов в предпросмотре — сверху и снизу.
EXTREMES = 3
# Части расходятся с итогом больше чем на столько (медиана по регионам) — предупреждение.
COVERAGE_TOLERANCE = 0.05
RELATIVE = DatasetSeries.Kind.RELATIVE.value
# Расчёты по частям одного целого: части — суммы одного типа; у суммы показателей — ещё
# и одной единицы.
PARTS_OPERATIONS = frozenset({"sum", "hhi", "structure", "lq", "structuralshift", "shiftshare"})


class CalcError(ValueError):
    """Поля панели заполнены не так: текст для человека."""


class CalcView(TemplateView):
    """
    Окно «Расчёт показателя» своего исследования: новые ряды встают в таблицу, их карты — на
    поле. Чужое исследование — 404.
    """

    template_name = "userdata/calc.html"
    study: Study | None = None
    # Таблица, со страницы которой открыто окно без исследования.
    dataset: Dataset | None = None

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        study = studies.owned(request).filter(public_id=kwargs["public_id"]).first()
        if study is None:
            raise Http404
        self.study = study
        return super().dispatch(request, *args, **kwargs)

    @property
    def calc_url(self) -> str:
        if self.study is not None:
            return reverse("userdata:calc", args=[self.study.public_id])
        assert self.dataset is not None
        return reverse("userdata:dataset-calc", args=[self.dataset.public_id])

    def get_template_names(self) -> list[str]:
        if self.request.headers.get("HX-Request"):
            return ["userdata/_calc_panel.html"]
        return [self.template_name]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        form = _form(request.GET)
        if not form["a"]:
            form["a"] = request.GET.get("series", "") or self.first_series()
        return self.render_to_response(self.panel(form, check=False))

    def first_series(self) -> str:
        """Окно со страницы таблицы — первый ряд этой таблицы; из исследования — пусто."""
        if self.dataset is None or self.dataset.current_version is None:
            return ""
        record = self.dataset.current_version.series.filter(derived="").order_by("order").first()
        return f"{routing.USER_PREFIX}{self.dataset.code}:{record.code}" if record else ""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        form = _form(request.POST)
        if request.POST.get("action") == "save":
            if not edit_allowed(request):
                return HttpResponse(status=429)
            try:
                return self.save(form)
            except (FormulaError, CalcError, studies.StudyError) as error:
                context = self.panel(form, check=False)
                context["error"] = str(error)
                return self.render_to_response(context)
        allowed = preview_allowed(request)
        return self.render_to_response(self.panel(form, check=allowed, throttled=not allowed))

    # --- Состояние панели ----------------------------------------------------------------

    def panel(
        self, form: dict[str, Any], *, check: bool, throttled: bool = False
    ) -> dict[str, Any]:
        """Контекст панели: каталог с причинами, поля операции, таблицы и предпросмотр."""
        options = self.series_options()
        known = {item["key"] for group in options for item in group["items"]}
        if form["a"] not in known:
            form["a"] = next(iter(known), "") if known else ""
        info = operations.info_of(form["a"]) if form["a"] else None
        available = operations.available_official()
        catalog = operations.grouped(info, available)
        code = form["op"]
        if code != FREE and code not in operations.BY_CODE:
            code = next(
                (
                    entry["operation"].code
                    for group in catalog
                    for entry in group["items"]
                    if not entry["reason"]
                ),
                FREE,
            )
        form["op"] = code
        operation = operations.BY_CODE.get(code)
        tables = self.tables()
        form["table"] = self.pick_table(form, tables)
        years = (
            list(range(info.first_year, info.last_year + 1))
            if info and info.first_year and info.last_year
            else []
        )
        _defaults(form, operation, years, available)
        context: dict[str, Any] = {
            "study": self.study,
            "dataset": self.dataset,
            "calc_url": self.calc_url,
            "form": form,
            "catalog": catalog,
            "operation": operation,
            "free": code == FREE,
            "options": options,
            "years": years,
            "groups": [
                (value, label)
                for value, label in operations.GROUP_CHOICES
                if available.get(operations.GROUP_DENOMINATORS[value][0])
            ],
            "tables": tables,
            "kinds": DatasetSeries.Kind.choices,
            "functions": formulas.FUNCTIONS,
            "page_title": _("Расчёт показателя"),
            **operations.arranged(catalog),
        }
        reason = operations.reason(operation, info, available) if operation and info else ""
        if reason:
            context["error"] = gettext("Этот расчёт не подходит показателю: %(reason)s.") % {
                "reason": reason
            }
            return context
        if operation is not None and operation.recount:
            context["recount_note"] = True
            return context
        if not check:
            if throttled:
                context["error"] = gettext("Проверок слишком много подряд — подождите минуту.")
            return context
        try:
            checks = self.checks(form, operation)
        except (FormulaError, CalcError) as error:
            context["error"] = str(error)
            return context
        first = checks[0]
        if operation is not None and operation.code in operations.COVERAGE_OPERATIONS:
            note = self.coverage_note(form, operation)
            if note:
                first.notes.append(note)
        context.update(
            checked=first,
            preview=preview(first, prefix="calc"),
            extremes=_extremes(first),
            others=[item.definition.title for item in checks[1:]],
            expression=formulas.display(first.definition.expression, first.definition.labels),
        )
        if not form["title"]:
            form["title_placeholder"] = first.definition.title
        return context

    def coverage_note(self, form: dict[str, Any], operation: operations.Operation) -> str:
        """Части не покрывают итог или больше него: доли и индекс выйдут неточными."""
        from apps.analytics.selectors import region_rows

        checked = formula_store.check(
            self.table(form["table"]),
            {
                "title": gettext("Покрытие итога"),
                "expression": operations.coverage_expression(_values(form, operation)),
                "kind": RELATIVE,
            },
        )
        subjects = {row["code"] for row in region_rows()}
        ratios = [
            value
            for code, _year, value in checked.outcome.rows
            if code in subjects and value is not None
        ]
        if not ratios:
            return ""
        share = median(ratios)
        if share < 1 - COVERAGE_TOLERANCE:
            return gettext(
                "Отмеченные части покрывают около %(share)s %% итога. Доли считаются от итога, "
                "поэтому индекс выйдет неточным. Отметьте все части итога."
            ) % {"share": ru_number(share * 100, 0)}
        if share > 1 + COVERAGE_TOLERANCE:
            return gettext(
                "Отмеченные части в сумме больше итога: около %(share)s %%. Похоже, среди них "
                "есть части частей, строки «в том числе»."
            ) % {"share": ru_number(share * 100, 0)}
        return ""

    def pick_table(self, form: dict[str, Any], tables: list[Dataset]) -> str:
        """Таблица нового ряда: выбранная, открывшая окно, таблица ряда A или первая."""
        codes = [item.code for item in tables]
        wanted = form["table"] or (self.dataset.code if self.dataset is not None else "")
        if wanted in codes:
            return wanted
        own = routing.dataset_code(form["a"]) if routing.is_user_key(form["a"]) else ""
        return own if own in codes else next(iter(codes), "")

    def series_options(self) -> list[dict[str, Any]]:
        """Ряды для полей: свои таблицы, затем официальные ряды поля исследования."""
        groups = own_option_groups(self.request)
        site = [
            key
            for block in (studies.blocks_of(self.study) if self.study is not None else [])
            for key in studies.series_keys(block)
            if not routing.is_user_key(key)
        ]
        if site:
            titles = {
                key: info.title
                for key in dict.fromkeys(site)
                if (info := operations.info_of(key)) is not None
            }
            items = [{"key": key, "title": title, "search": title} for key, title in titles.items()]
            if items:
                groups.append({"title": gettext("Показатели сайта на поле"), "items": items})
        return groups

    def tables(self) -> list[Dataset]:
        """Свои собранные таблицы: формула живёт в одной из них."""
        return [
            dataset
            for dataset in access.owned(self.request).select_related("current_version")
            if dataset.current_version is not None
            and dataset.current_version.state == DatasetVersion.State.BUILT
        ]

    def table(self, code: str) -> Dataset:
        found = next((item for item in self.tables() if item.code == code), None)
        if found is None:
            raise CalcError(gettext("Новый показатель сохраняется в свою таблицу — загрузите её."))
        return found

    # --- Расчёт и сохранение -------------------------------------------------------------

    def checks(
        self, form: dict[str, Any], operation: operations.Operation | None
    ) -> list[formula_store.Check]:
        """Проверка всех новых рядов расчёта по нынешним данным."""
        dataset = self.table(form["table"])
        if operation is None:
            data = {
                "title": form["title"] or gettext("Новый показатель"),
                "expression": form["expression"],
                "unit": form["unit"],
                "kind": form["kind"],
            }
            return [formula_store.check(dataset, data)]
        assert operation.outputs is not None
        values = _values(form, operation)
        outputs = operation.outputs(values)
        single = len(outputs) == 1
        return [
            formula_store.check(
                dataset,
                {
                    "title": (form["title"] if single else "") or output.title,
                    "expression": output.expression,
                    "unit": (form["unit"] if single else "") or output.unit,
                    "kind": output.kind,
                    "operation": operation.code,
                    "parameters": _parameters(form, operation),
                },
            )
            for output in outputs
        ]

    def save(self, form: dict[str, Any]) -> HttpResponse:
        """
        Сохранить расчёт: в исследовании — карты новых рядов на поле, без него — к новому
        показателю на странице таблицы.
        """
        context = self.panel(form, check=False)
        if context.get("error"):
            raise CalcError(context["error"])
        operation = context["operation"]
        if operation is not None and operation.recount:
            if self.study is None:
                lab.compute(self.request, form["a"], operation.recount)
                return self.to_table(self.table(routing.dataset_code(form["a"])), "")
            block = lab.add(
                self.request, self.study, f"{lab.COMPUTE_PREFIX}{operation.recount}", form["a"]
            )
            return self.done([block])
        checks = self.checks(form, operation)
        dataset = self.table(form["table"])
        for item in checks:
            formula_store.save(dataset, item.definition)
        version = dataset.current_version
        assert version is not None
        jobs.start(version, jobs.BUILD)
        if self.study is None:
            return self.to_table(dataset, checks[0].definition.code)
        keys = [f"{routing.USER_PREFIX}{dataset.code}:{item.definition.code}" for item in checks]
        blocks = studies.append_all(
            self.study,
            [studies.view_block("map", urlencode({"series": key})) for key in keys],
        )
        return self.done(blocks)

    def to_table(self, dataset: Dataset, code: str) -> HttpResponse:
        """На страницу таблицы — к новому показателю."""
        url = reverse("userdata:dataset", args=[dataset.public_id])
        return self.go(f"{url}?{urlencode({'show': code})}" if code else url)

    def done(self, blocks: list[dict[str, Any]]) -> HttpResponse:
        """В исследование — к первой новой карточке."""
        assert self.study is not None
        url = reverse("userdata:study", args=[self.study.public_id])
        first = blocks[0]["id"]
        return self.go(f"{url}?{urlencode({'added': first})}#block-{first}")

    def go(self, target: str) -> HttpResponse:
        """Переход после сохранения: окном — заголовком HX-Redirect, страницей — 302."""
        if self.request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = target
            return response
        return redirect(target)


def _form(data: QueryDict) -> dict[str, Any]:
    form: dict[str, Any] = {name: " ".join(data.get(name, "").split()) for name in TEXT_FIELDS}
    form["expression"] = data.get("expression", "").strip()
    form["parts"] = list(dict.fromkeys(item for item in data.getlist("parts") if item))
    return form


def _defaults(
    form: dict[str, Any],
    operation: operations.Operation | None,
    years: list[int],
    available: dict[str, bool],
) -> None:
    """Пустые поля операции — значениями по умолчанию: годы ряда, первый выбор."""
    if operation is None:
        form["kind"] = (
            form["kind"] if form["kind"] in dict(DatasetSeries.Kind.choices) else RELATIVE
        )
        return
    for item in operation.fields:
        value = form.get(item.name, "")
        if item.kind == operations.YEAR:
            if not value.isdigit() or int(value) not in years:
                default = years[-1] if item.name == "last" else (years[0] if years else "")
                form[item.name] = str(default)
        elif item.kind == operations.CHOICE:
            choices = [
                code
                for code, _label in item.choices
                if item.name != "group" or available.get(operations.GROUP_DENOMINATORS[code][0])
            ]
            if value not in choices:
                form[item.name] = choices[0] if choices else ""


def _values(form: dict[str, Any], operation: operations.Operation) -> operations.Values:
    """Значения полей операции из формы; ошибка — словами."""
    values = operations.Values()
    for item in operation.fields:
        raw = form.get(item.name, "")
        if item.kind == operations.SERIES:
            if not raw:
                raise CalcError(gettext("Выберите показатель «%(label)s».") % {"label": item.label})
            values.series[item.name] = raw
        elif item.kind == operations.SERIES_LIST:
            limit = (
                operations.SHIFT_SHARE_PARTS
                if operation.code == "shiftshare"
                else operations.MAX_PARTS
            )
            parts = form["parts"][:limit]
            if len(parts) < 2:  # noqa: PLR2004 — составляющих хотя бы две
                raise CalcError(gettext("Отметьте хотя бы две составляющие."))
            values.lists[item.name] = parts
        elif item.kind == operations.YEAR:
            if not raw.isdigit():
                raise CalcError(gettext("Выберите «%(label)s».") % {"label": item.label})
            values.numbers[item.name] = int(raw)
        else:
            values.choices[item.name] = raw
    if "first" in values.numbers and values.numbers["first"] >= values.numbers.get("last", 0):
        raise CalcError(gettext("Первый год периода должен быть раньше последнего."))
    keys = [*values.series.values(), *(key for items in values.lists.values() for key in items)]
    values.info = {key: info for key in keys if (info := operations.info_of(key)) is not None}
    _check_parts(operation, list(values.info.values()))
    return values


def _check_parts(operation: operations.Operation, found: list[operations.Info]) -> None:
    """Части одного целого — суммы одного типа, у суммы показателей — и одной единицы."""
    if operation.code not in PARTS_OPERATIONS:
        return
    problem = semantics.parts_reason(
        [item.type for item in found],
        [item.unit for item in found] if operation.code == "sum" else [],
    )
    if problem:
        raise CalcError(problem)


def _parameters(form: dict[str, Any], operation: operations.Operation) -> dict[str, Any]:
    """Поля операции для записи происхождения ряда."""
    found: dict[str, Any] = {}
    for item in operation.fields:
        found[item.name] = (
            form["parts"] if item.kind == operations.SERIES_LIST else form.get(item.name, "")
        )
    return found


def _extremes(checked: formula_store.Check) -> dict[str, Any]:
    """Наибольшие и наименьшие значения года предпросмотра — по субъектам."""
    from apps.analytics.selectors import region_rows

    names = {row["code"]: row["name"] for row in region_rows()}
    by_year: dict[int, dict[str, float]] = {}
    for code, year, value in checked.outcome.rows:
        if code in names:
            by_year.setdefault(int(year), {})[code] = value
    if not by_year:
        return {}
    year = max(by_year, key=lambda item: (len(by_year[item]), item))
    ordered = sorted(by_year[year].items(), key=lambda item: item[1], reverse=True)

    def rows(items: list[tuple[str, float]]) -> list[dict[str, str]]:
        return [{"name": names[code], "value": ru_number(value)} for code, value in items]

    return {
        "year": year,
        "top": rows(ordered[:EXTREMES]),
        "bottom": rows(ordered[-EXTREMES:][::-1]) if len(ordered) > EXTREMES else [],
    }


class DatasetCalcView(CalcView):
    """
    То же окно «Расчёт показателя» со страницы своей таблицы, без исследования: новый
    показатель встаёт в таблицу. Чужая или несобранная таблица — 404.
    """

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        dataset = (
            access.owned(request)
            .select_related("current_version")
            .filter(public_id=kwargs["public_id"])
            .first()
        )
        version = dataset.current_version if dataset is not None else None
        if version is None or version.state != DatasetVersion.State.BUILT:
            raise Http404
        self.dataset = dataset
        return TemplateView.dispatch(self, request, *args, **kwargs)
