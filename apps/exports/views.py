"""Выдача документов файлом по параметрам страницы."""

from __future__ import annotations

import logging

from django.apps import apps
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.views.generic import View

from apps.core.throttle import allow
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.routing import exportable, is_user_key

from .constants import REPORT_KINDS_BY_CODE, ExportFormat
from .reports import ReportParameterError
from .services import Document, DocumentRequestError, build_document, default_parameters

logger = logging.getLogger(__name__)

# Предел документов с одного адреса за окно в секундах.
DOCUMENT_LIMIT = 60
DOCUMENT_WINDOW = 600


class DocumentView(View):
    """
    Документ по параметрам страницы; формат — параметром ``format``, у ``data.csv`` — CSV.

    Отказ объясняется текстом, а не пустым файлом.
    """

    fixed_format: str = ""

    def get(self, request: HttpRequest) -> HttpResponse:
        """Собрать документ и отдать его файлом."""
        kind = request.GET.get("kind", "")
        if kind not in REPORT_KINDS_BY_CODE:
            raise Http404("Неизвестный вид выгрузки")
        export_format = self.fixed_format or request.GET.get("format", ExportFormat.CSV)
        if export_format not in ExportFormat.values:
            raise Http404("Неизвестный формат выгрузки")

        # Ряд чужой таблицы — как несуществующий: 404, а не отказ с объяснением; так же —
        # таблица, открытая закрытой ссылкой без права скачивать.
        series = request.GET.get("series", "")
        if is_user_key(series) and not exportable(series):
            raise Http404("Ряд не найден")

        if not allow(request, "documents", limit=DOCUMENT_LIMIT, window=DOCUMENT_WINDOW):
            return _refusal("Слишком много выгрузок подряд. Повторите через несколько минут.", 429)

        try:
            document = build_document(
                kind,
                export_format,
                default_parameters(kind, request.GET),
                site_url=f"{request.scheme}://{request.get_host()}",
            )
        except (DocumentRequestError, ReportParameterError) as error:
            return _refusal(str(error), 400)
        except WarehouseNotBuiltError:
            return _refusal("Данные ещё не загружены", 503)

        # За числа из чужой таблицы сайт не ручается: подписываются только ряды склада.
        if not is_user_key(series):
            _sign(document, kind, export_format)

        response = HttpResponse(document.payload, content_type=document.content_type)
        response["Content-Disposition"] = document.disposition
        # Происхождение — заголовком ``Link`` на исходную страницу, не строкой в файле.
        back = request.META.get("HTTP_REFERER", "")
        if back:
            response["Link"] = f'<{back}>; rel="describedby"'
        return response


class DataDownloadView(DocumentView):
    """Числа таблицей по короткому адресу ``data.csv``."""

    fixed_format = ExportFormat.CSV


def _sign(document: Document, kind: str, export_format: str) -> None:
    """Подписать документ и записать в реестр выданных; сбой подписи не мешает выдаче."""
    if not apps.is_installed("apps.attest"):
        return
    from apps.attest import services as attest

    try:
        attest.issue(
            document.payload, kind=kind, export_format=export_format, filename=document.filename
        )
    except DatabaseError:
        logger.exception("Документ выдан без записи в реестр выданных")


def _refusal(message: str, status: int) -> HttpResponse:
    """Ответ с объяснением отказа."""
    return HttpResponse(message, status=status, content_type="text/plain; charset=utf-8")
