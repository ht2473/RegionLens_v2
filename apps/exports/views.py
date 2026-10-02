"""Выдача документов файлом по параметрам страницы."""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.views.generic import View

from apps.core.throttle import allow
from apps.warehouse.duckdb_client import WarehouseNotBuiltError

from .constants import REPORT_KINDS_BY_CODE, ExportFormat
from .reports import ReportParameterError
from .services import DocumentRequestError, build_document, default_parameters

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


def _refusal(message: str, status: int) -> HttpResponse:
    """Ответ с объяснением отказа."""
    return HttpResponse(message, status=status, content_type="text/plain; charset=utf-8")
