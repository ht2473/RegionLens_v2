"""Постраничная выдача API с ограниченным сверху размером страницы."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from rest_framework.pagination import LimitOffsetPagination, PageNumberPagination
from rest_framework.response import Response


class CatalogPagination(PageNumberPagination):
    """Постраничная выдача справочников."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500

    def get_paginated_response(self, data: Any) -> Response:
        """Дополнить ответ числом страниц: клиенту нужно знать, сколько их всего."""
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("pages", self.page.paginator.num_pages),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("results", data),
                ]
            )
        )


class ObservationPagination(LimitOffsetPagination):
    """Выдача наблюдений порциями со смещением: клиент хранит достигнутую позицию."""

    default_limit = 500
    max_limit = 5000
    limit_query_param = "limit"
    offset_query_param = "offset"
