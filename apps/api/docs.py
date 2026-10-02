"""Страница описания программного интерфейса; адреса точек — обратным разрешением маршрутов."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.core.navigation import Crumb
from apps.core.templatetags.formatting import ru_number
from apps.core.views import BreadcrumbMixin


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Описание одной точки интерфейса."""

    url_name: str
    title: Any
    summary: Any
    parameters: tuple[tuple[str, Any], ...] = ()
    example: Any = ""
    detail_hint: Any = ""

    @property
    def path(self) -> str:
        """Адрес точки."""
        return reverse(self.url_name)


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint(
        url_name="api:v1:territory-list",
        title=_("Территории"),
        summary=_(
            "Справочник субъектов, федеральных округов и страны в целом: коды, "
            "принадлежность округу, координаты, часовой пояс."
        ),
        parameters=(
            ("level", _("region, federal_district или country")),
            ("is_aggregate", _("исключить составные территории: false")),
        ),
        example="/api/v1/territories/?level=region",
        detail_hint=_("Карточка территории: /api/v1/territories/<код>/"),
    ),
    Endpoint(
        url_name="api:v1:indicator-list",
        title=_("Показатели"),
        summary=_(
            "Каталог показателей вместе с их рядами: разделы, направленность, "
            "покрытие, годы наблюдений."
        ),
        parameters=(
            ("section__slug", _("адресный идентификатор раздела")),
            ("is_featured", _("только ключевые показатели: true")),
            ("search", _("поиск по названию на русском или английском и по коду")),
        ),
        example=_("/api/v1/indicators/?search=заработная"),
        detail_hint=_("Карточка показателя: /api/v1/indicators/<код>/"),
    ),
    Endpoint(
        url_name="api:v1:series-list",
        title=_("Ряды наблюдений"),
        summary=_(
            "Единица анализа — пара «показатель + разрез». Значения, рейтинги "
            "и расчёты адресуются ключом ряда."
        ),
        parameters=(
            ("is_analysis_ready", _("только ряды, пригодные для сравнений: true")),
            ("indicator__section__slug", _("раздел показателя")),
        ),
        example="/api/v1/series/?is_analysis_ready=true",
        detail_hint=_("Карточка ряда: /api/v1/series/<ключ>/"),
    ),
    Endpoint(
        url_name="api:v1:observations",
        title=_("Наблюдения"),
        summary=_(
            "Значения ряда по территориям и годам вместе с признаком качества: "
            "observed, no_data, hidden или not_applicable. Пропуск «нет данных» "
            "и пропуск «сведения скрыты» — разные вещи, и различать их обязательно."
        ),
        parameters=(
            ("series", _("ключ ряда — обязательный параметр")),
            ("territory", _("код территории; без него возвращаются все субъекты")),
            ("year_from, year_to", _("границы периода")),
            ("limit, offset", _("размер порции и смещение")),
        ),
        example="/api/v1/observations/?series=Y477110378:00&year_from=2015",
        detail_hint=_(
            "У каждого значения — источник (dataset — набор, иначе код источника), выпуск "
            "и признаки: preliminary — предварительное, computed — рассчитано проектом, "
            "frozen_denominator — на жителя по численности последнего опубликованного года."
        ),
    ),
    Endpoint(
        url_name="api:v1:monthly",
        title=_("Значения по месяцам"),
        summary=_(
            "Помесячный слой: зарплата, цены, безработица из бюллетеня Росстата, ипотека "
            "и кредиты Банка России, малый и средний бизнес по реестру ФНС. Вид значения: "
            "level — за месяц или на его конец, ytd — с начала года, yoy — к тому же периоду "
            "прошлого года."
        ),
        parameters=(
            ("series", _("ключ годового ряда — обязательный параметр")),
            ("territory", _("код территории; без него возвращаются все субъекты")),
            ("kind", _("level, ytd или yoy")),
            ("year_from, year_to", _("границы периода")),
            ("limit, offset", _("размер порции и смещение")),
        ),
        example="/api/v1/monthly/?series=RL_MORTGAGE_COUNT:00&territory=RU&kind=ytd",
    ),
    Endpoint(
        url_name="api:v1:source-list",
        title=_("Источники"),
        summary=_(
            "Внешние источники, выпуски которых собирает проект: издатель, условия "
            "использования, последняя проверка и последний выпуск."
        ),
        example="/api/v1/sources/",
        detail_hint=_("Карточка источника: /api/v1/sources/<код>/"),
    ),
    Endpoint(
        url_name="api:v1:releases",
        title=_("Выпуски источников"),
        summary=_(
            "Выпуски в архиве проекта: адрес, контрольная сумма SHA-256, дата получения "
            "и итог разбора."
        ),
        parameters=(
            ("source__code", _("код источника")),
            ("status", _("parsed, failed или fetched")),
        ),
        example="/api/v1/releases/?source__code=cbr",
    ),
    Endpoint(
        url_name="api:v1:rankings",
        title=_("Рейтинги"),
        summary=_(
            "Позиции субъектов по показателю за год с изменением к предыдущему году "
            "и отношением к среднему по стране."
        ),
        parameters=(
            ("series", _("ключ ряда — обязательный параметр")),
            ("year", _("год рейтинга; по умолчанию последний доступный")),
            ("order", _("desc или asc")),
        ),
        example="/api/v1/rankings/?series=Y477110378:00&year=2023",
    ),
)


class ApiDocsView(BreadcrumbMixin, TemplateView):
    """Страница описания программного интерфейса."""

    template_name = "api/docs.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Программный интерфейс")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать описание интерфейса."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Программный интерфейс")
        context["endpoints"] = ENDPOINTS
        context["base_url"] = self.request.build_absolute_uri("/api/v1/")
        context["anonymous_rate"] = anonymous_rate()
        return context


# Период предела словами по первой букве записи DRF («1000/hour»).
RATE_PERIODS = {
    "s": _("в секунду"),
    "m": _("в минуту"),
    "h": _("в час"),
    "d": _("в сутки"),
}


def rate_in_words(rate: str) -> str:
    """Предел числа обращений словами: «1000/hour» → «1 000 обращений в час»."""
    count, _slash, period = rate.partition("/")
    words = RATE_PERIODS.get(period[:1].lower())
    if not count.strip().isdigit() or words is None:
        return rate
    return _("%(count)s обращений %(period)s") % {
        "count": ru_number(int(count), 0),
        "period": words,
    }


def anonymous_rate() -> str:
    """Предел обращений без входа словами — из настроек интерфейса."""
    # Приведение типа — ради вложенного отображения пределов.
    rest_settings = cast("dict[str, Any]", settings.REST_FRAMEWORK)
    return rate_in_words(rest_settings["DEFAULT_THROTTLE_RATES"].get("anon", ""))
