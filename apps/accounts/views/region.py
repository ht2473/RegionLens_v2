"""Кнопка «Это мой регион»: выбор и сброс для гостя и вошедшего."""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View

from apps.core.utils.redirects import safe_back

from ..region import find_region, remember_region


class MyRegionView(View):
    """Запомнить регион или забыть его; HTMX получает новую кнопку, форма — переход назад."""

    def post(self, request: HttpRequest) -> HttpResponse:
        """``region`` — код субъекта; ``clear`` — забыть регион."""
        back = safe_back(request, reverse("core:home"))
        code = request.POST.get("region", "")
        region = None if request.POST.get("clear") else find_region(code)
        if region is None and not request.POST.get("clear"):
            messages.error(request, _("Регион не найден"))
            return HttpResponseRedirect(back)

        if request.headers.get("HX-Request"):
            territory = region or find_region(code)
            response = render(
                request,
                "accounts/partials/_my_region_button.html",
                {"territory": territory, "is_mine": region is not None, "back": back},
            )
        else:
            response = HttpResponseRedirect(back)
            if region is not None:
                messages.success(request, _("«%(name)s» — ваш регион") % {"name": region.name})
            else:
                messages.success(request, _("Мой регион сброшен"))
        remember_region(request, response, region)
        return response
