"""Страница «Проверить документ», выдача подписи и сертификатов, по которым проверяют без сайта."""

from __future__ import annotations

import urllib.parse
from typing import Any

from django.http import Http404, HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, View

from apps.core.navigation import Crumb
from apps.core.throttle import allow
from apps.core.views import BreadcrumbMixin

from . import keys, services
from .forms import VerifyForm
from .models import IssuedDocument

# Предел проверок с одного адреса за окно в секундах: каждая — загрузка файла.
VERIFY_LIMIT = 30
VERIFY_WINDOW = 600

SIGNATURE_TYPE = "application/pkcs7-signature"


class VerifyView(BreadcrumbMixin, FormView):
    """Проверка документа: файл не сохраняется, ответ — на той же странице."""

    template_name = "attest/verify.html"
    form_class = VerifyForm

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Проверить документ")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Заголовок и сведения о подписи сайта."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Проверить документ")
        context["trust"] = keys.trust()
        return context

    def form_valid(self, form: VerifyForm) -> HttpResponse:
        """Проверить загруженный документ и показать исход."""
        if not allow(self.request, "attest-verify", limit=VERIFY_LIMIT, window=VERIFY_WINDOW):
            form.add_error(
                None, _("Слишком много проверок подряд. Повторите через несколько минут.")
            )
            return self.form_invalid(form)
        document = form.cleaned_data["document"]
        signature = form.cleaned_data.get("signature")
        outcome = services.check(document.read(), signature.read() if signature else None)
        context = self.get_context_data(form=VerifyForm(), outcome=outcome, name=document.name)
        return self.render_to_response(context)


class SignatureView(View):
    """Подпись выданного документа файлом ``.p7s`` — для проверки без сайта."""

    def get(self, request: HttpRequest, sha256: str) -> HttpResponse:  # noqa: ARG002
        """Отдать подпись по хэшу документа."""
        record = IssuedDocument.objects.filter(sha256=sha256.lower()).first()
        if record is None:
            raise Http404("Такого документа сайт не выдавал")
        name = f"{record.filename}.p7s"
        quoted = urllib.parse.quote(name)
        response = HttpResponse(bytes(record.signature), content_type=SIGNATURE_TYPE)
        response["Content-Disposition"] = (
            f"attachment; filename=\"{name}\"; filename*=UTF-8''{quoted}"
        )
        return response


class TrustFileView(View):
    """Корневой сертификат, сертификат службы подписи и список отзыва."""

    def get(self, request: HttpRequest, name: str) -> HttpResponse:  # noqa: ARG002
        """Отдать открытый файл каталога подписи."""
        content_type = keys.PUBLIC_FILES.get(name)
        path = keys.directory() / name
        if content_type is None or not path.is_file():
            raise Http404("Файл не опубликован")
        response = HttpResponse(path.read_bytes(), content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="regionlens-{name}"'
        return response
