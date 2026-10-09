"""Витрина, страницы «О проекте» и «Условия использования», служебные проверки состояния."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.cache import patch_cache_control
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from apps.catalog.indicator import series_descriptor
from apps.catalog.models import SeriesBreak, Territory
from apps.core import documents, showcase, structured_data
from apps.core.navigation import SITE_SECTIONS, Crumb, build_breadcrumbs
from apps.search.constants import EXAMPLES as SEARCH_EXAMPLES
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import warehouse_summary

logger = logging.getLogger(__name__)


class BreadcrumbMixin:
    """Добавляет в контекст путь к странице из ``crumbs`` или ``get_crumbs()``."""

    crumbs: tuple[Crumb, ...] = ()

    # Атрибуты представления, к которому подмешан слой, — для проверки типов.
    if TYPE_CHECKING:
        request: HttpRequest

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Вернуть звенья пути к текущей странице."""
        return self.crumbs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст путём к странице."""
        context = super().get_context_data(**kwargs)  # type: ignore[misc]
        context["breadcrumbs"] = build_breadcrumbs(self.request, *self.get_crumbs())
        return context


class HomeView(TemplateView):
    """Витрина: живая карта, вопросы с готовым ответом и главное о стране."""

    template_name = "core/home.html"

    def get_template_names(self) -> list[str]:
        """Смена показателя на живой карте отдаёт только фрагмент карты."""
        if self.request.headers.get("HX-Target") == showcase.LIVE_MAP_ID:
            return ["core/partials/_live_map.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать данные витрины."""
        from apps.accounts.region import my_region

        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Главная")
        context["structured_data"] = structured_data.site(self.request)
        context["break_count"] = SeriesBreak.objects.count()
        context["live_map_id"] = showcase.LIVE_MAP_ID
        # Первый экран ищет сам: поле поиска в шапке не повторяет его.
        context["hide_header_search"] = True
        context["search_examples"] = SEARCH_EXAMPLES

        try:
            item = showcase.resolve_live_series(self.request.GET.get("series"))
            region = my_region(self.request)
            context["live"] = (
                showcase.live_map(
                    item, self.request.GET.get("year"), region.code if region else None
                )
                if item
                else None
            )
            context["live_choices"] = [
                {"series": choice, "active": item is not None and choice.key == item.key}
                for choice in showcase.live_series()
            ]
            context["summary"] = warehouse_summary()
            fragment = self.request.headers.get("HX-Target") == showcase.LIVE_MAP_ID
            context["my_region_brief"] = None if fragment else _my_region_brief(self.request)
            context["questions"] = showcase.answered_questions(region.code if region else None)
            context["tiles"] = showcase.country_tiles()
            context["warehouse_ready"] = True
        except WarehouseNotBuiltError as error:
            logger.warning("Склад не собран: %s", error)
            context["warehouse_ready"] = False

        return context


def _my_region_brief(request: HttpRequest) -> dict[str, Any] | None:
    """Полоса «Мой регион» над живой картой: главное словами и последние месяцы."""
    from apps.accounts.region import my_region
    from apps.catalog.brief import BAND_NOW, region_brief

    region = my_region(request)
    if region is None:
        return None
    return region_brief(region, metrics=0, now=BAND_NOW)


@require_GET
def home_frames(request: HttpRequest) -> JsonResponse:
    """
    Кадры живой карты по всем годам ряда для ползунка и воспроизведения.

    Адрес несёт отпечаток склада и набора, поэтому ответ кэшируется надолго.
    """
    try:
        item = series_descriptor(request.GET.get("series"))
        if item is None:
            return JsonResponse({"error": "no series"}, status=404)
        frames = showcase.live_frames(item)
    except WarehouseNotBuiltError:
        # Без склада кадров нет; сценарий оставит карту на встроенном годе.
        return JsonResponse({"error": "warehouse not built"}, status=503)
    response = JsonResponse(
        {
            "series": frames["series"],
            "years": frames["years"],
            "frames": frames["frames"],
        },
        json_dumps_params={"ensure_ascii": False, "separators": (",", ":")},
    )
    patch_cache_control(response, public=True, max_age=showcase.FRAMES_MAX_AGE)
    return response


class AboutView(BreadcrumbMixin, TemplateView):
    """О проекте: назначение, первые шаги, обозначения, разделы, источник и реквизиты."""

    template_name = "core/about.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("О проекте")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст сводкой о данных и перечнем разделов."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("О проекте")
        context["sections"] = SITE_SECTIONS
        try:
            context["summary"] = warehouse_summary()
        except WarehouseNotBuiltError:
            context["summary"] = None
        return context


class DocumentView(BreadcrumbMixin, TemplateView):
    """Документ о правилах сайта: редакция, сроки хранения и сведения, которые в нём названы."""

    title: Any = ""
    revision: date

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=self.title),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст редакцией, сроками хранения и адресом сайта."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = self.title
        context["revision"] = self.revision
        context["site_url"] = f"{self.request.scheme}://{self.request.get_host()}"
        context["session_days"] = settings.SESSION_COOKIE_AGE // (24 * 3600)
        context["backup_days"] = documents.backup_retention_days()
        context["access_log_days"] = documents.ACCESS_LOG_DAYS
        context["ticket_trace_days"] = documents.TICKET_TRACE_DAYS
        context["ticket_contact_days"] = documents.TICKET_CONTACT_DAYS
        context["destruction_days"] = documents.DESTRUCTION_DAYS
        context["lockout_hours"] = settings.AXES_COOLOFF_TIME
        context["guest_hours"] = settings.USERDATA_GUEST_HOURS
        context["upload_mb"] = settings.USERDATA_UPLOAD_MAX_BYTES // (1024 * 1024)
        context["table_limit"] = settings.USERDATA_MAX_DATASETS
        context["quota_mb"] = settings.USERDATA_QUOTA_BYTES // (1024 * 1024)
        context["error_tracking"] = bool(getattr(settings, "SENTRY_DSN", ""))
        return context


class TermsView(DocumentView):
    """Условия использования: источники и ссылка на них, расчёты, API, учётная запись."""

    template_name = "core/terms.html"
    title = _("Условия использования")
    revision = documents.TERMS_REVISION

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст условиями источников и пределом программного интерфейса."""
        from apps.api.docs import anonymous_rate
        from apps.sources.collect import SOURCES

        context = super().get_context_data(**kwargs)
        english = get_language() == "en"
        context["source_terms"] = [
            {
                "title": module.title_en if english else module.title_ru,
                "publisher": module.publisher_en if english else module.publisher_ru,
                "licence": module.licence_en if english else module.licence_ru,
                "url": module.page_url,
            }
            for module in SOURCES.values()
        ]
        context["api_rate"] = anonymous_rate()
        return context


class PrivacyView(DocumentView):
    """Политика обработки персональных данных (ч. 2 ст. 18.1 152-ФЗ)."""

    template_name = "core/privacy.html"
    title = _("Политика обработки персональных данных")
    revision = documents.PRIVACY_REVISION


class ConsentView(DocumentView):
    """Текст согласия на обработку персональных данных, на который ссылаются флажки форм."""

    template_name = "core/consent.html"
    title = _("Согласие на обработку персональных данных")
    revision = documents.CONSENT_REVISION


# ---------------------------------------------------------------------------------------
# Служебные проверки
# ---------------------------------------------------------------------------------------


@require_GET
@never_cache
def healthz(request: HttpRequest) -> HttpResponse:  # noqa: ARG001
    """Проверка живости процесса без обращения к внешним ресурсам (для Docker)."""
    return HttpResponse("ok", content_type="text/plain; charset=utf-8")


@require_GET
@never_cache
def readyz(request: HttpRequest) -> JsonResponse:  # noqa: ARG001
    """Проверка готовности: база данных, справочник территорий и собранный склад."""
    checks: dict[str, dict[str, Any]] = {}

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = {"ok": True}
    except Exception as error:  # причина отказа важна в ответе проверки
        checks["database"] = {"ok": False, "error": str(error)}

    territory_count = Territory.objects.count()
    checks["reference"] = {
        "ok": territory_count > 0,
        "territories": territory_count,
        "hint": None if territory_count else "Выполните: python manage.py seed_reference",
    }

    warehouse_path = settings.DUCKDB_PATH
    checks["warehouse"] = {
        "ok": warehouse_path.exists(),
        "path": str(warehouse_path),
        "hint": None if warehouse_path.exists() else "Выполните: python manage.py etl_build --full",
    }

    ready = all(check["ok"] for check in checks.values())
    return JsonResponse(
        {"ready": ready, "version": settings.PROJECT_VERSION, "checks": checks},
        status=200 if ready else 503,
        json_dumps_params={"ensure_ascii": False},
    )
