"""
Точки программного интерфейса, только на чтение.

Справочники — наборами представлений поверх ORM, наблюдения и рейтинги — поверх склада.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status, viewsets
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Indicator, Series, Territory
from apps.core.search import search_q
from apps.sources.models import Release, Source
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import (
    available_years,
    ranked_years,
    ranking_table,
    series_observations,
)
from apps.warehouse.queries.monthly import month_observations

from .pagination import ObservationPagination
from .serializers import (
    IndicatorSerializer,
    MonthlyObservationSerializer,
    MonthlyQuerySerializer,
    ObservationQuerySerializer,
    ObservationSerializer,
    RankingQuerySerializer,
    RankingRowSerializer,
    ReleaseSerializer,
    SeriesSerializer,
    SourceSerializer,
    TerritorySerializer,
)

logger = logging.getLogger(__name__)


class WarehouseMixin:
    """Ответ 503 с объяснением, пока склад не собран."""

    def warehouse_unavailable(self, error: WarehouseNotBuiltError) -> Response:
        """Ответ о временной недоступности данных; путь к файлу склада — только в журнал."""
        logger.warning("Склад не собран: %s", error)
        return Response(
            {
                "error": "Данные ещё не загружены",
                "status": status.HTTP_503_SERVICE_UNAVAILABLE,
                "code": "warehouse_not_built",
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


@extend_schema(tags=["Территории"])
class TerritoryViewSet(viewsets.ReadOnlyModelViewSet):
    """Справочник территорий: субъекты, федеральные округа и страна в целом."""

    serializer_class = TerritorySerializer
    lookup_field = "code"
    filterset_fields = ["level", "territory_type", "is_aggregate"]

    def get_queryset(self) -> QuerySet[Territory]:
        """Территории в порядке показа."""
        return Territory.objects.select_related("parent").order_by("display_order", "name_ru")


@extend_schema(tags=["Показатели"])
class IndicatorViewSet(viewsets.ReadOnlyModelViewSet):
    """Каталог показателей вместе с их рядами."""

    serializer_class = IndicatorSerializer
    lookup_field = "code"
    filterset_fields = ["section__slug", "is_featured", "polarity"]

    def get_queryset(self) -> QuerySet[Indicator]:
        """Показатели с предзагруженными разделом и рядами."""
        queryset = (
            Indicator.objects.select_related("section").prefetch_related("series").order_by("code")
        )
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(search_q(search, "name_ru", "name_en", "code"))
        return queryset

    # Переопределён ради описания параметра поиска в схеме: его разбирает не фильтр.
    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                description="Поиск по названию или коду показателя",
                required=False,
                type=str,
            )
        ]
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Перечень показателей."""
        return super().list(request, *args, **kwargs)


@extend_schema(tags=["Показатели"])
class SeriesViewSet(viewsets.ReadOnlyModelViewSet):
    """Ряды наблюдений — пары «показатель + разрез» с ключом ряда."""

    serializer_class = SeriesSerializer
    lookup_field = "key"
    lookup_value_regex = "[^/]+"
    filterset_fields = ["is_analysis_ready", "has_subsection", "indicator__section__slug"]

    def get_queryset(self) -> QuerySet[Series]:
        """Ряды с предзагруженными показателем, разделом и единицей."""
        return Series.objects.select_related("indicator", "indicator__section", "unit").order_by(
            "key"
        )


@extend_schema(
    tags=["Наблюдения"],
    parameters=[ObservationQuerySerializer],
    responses=ObservationSerializer(many=True),
)
class ObservationListView(WarehouseMixin, APIView):
    """Значения ряда наблюдений; без территории — по всем субъектам, без округов и страны."""

    pagination_class = ObservationPagination

    def get(self, request: Request) -> Response:
        """Выдать наблюдения порцией со смещением."""
        query = ObservationQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        parameters = query.validated_data

        paginator = self.pagination_class()
        limit = paginator.get_limit(request)
        offset = paginator.get_offset(request)

        try:
            rows, total = series_observations(
                parameters["series"],
                territory_code=parameters.get("territory"),
                first_year=parameters.get("year_from"),
                last_year=parameters.get("year_to"),
                limit=limit,
                offset=offset,
            )
        except WarehouseNotBuiltError as error:
            return self.warehouse_unavailable(error)

        if not rows and offset == 0:
            raise NotFound(_("По заданным условиям наблюдений нет"))

        return Response(
            {
                "count": total,
                "limit": limit,
                "offset": offset,
                "results": ObservationSerializer(rows, many=True).data,
            }
        )


@extend_schema(
    tags=["Рейтинги"],
    parameters=[RankingQuerySerializer],
    responses=RankingRowSerializer(many=True),
)
class RankingView(WarehouseMixin, APIView):
    """Рейтинг субъектов по ряду за год с изменением позиции к предыдущему году."""

    def get(self, request: Request) -> Response:
        """Выдать рейтинг целиком, без разбиения на страницы."""
        query = RankingQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        parameters = query.validated_data
        series_key = parameters["series"]
        ascending = parameters.get("order") == "asc"

        try:
            years = ranked_years(series_key) or available_years(series_key)
            if not years:
                raise NotFound(_("Рейтинг по этому ряду не рассчитан"))

            year = parameters.get("year") or years[-1]
            if year not in years:
                raise ValidationError(
                    {
                        "year": _("Нет рейтинга за этот год. Доступны: %s")
                        % ", ".join(str(item) for item in years)
                    }
                )

            previous_year = next((item for item in reversed(years) if item < year), None)
            rows = ranking_table(
                series_key,
                year,
                previous_year=previous_year,
                ascending=ascending,
            )
        except WarehouseNotBuiltError as error:
            return self.warehouse_unavailable(error)

        rank_key = "rank_asc" if ascending else "rank_desc"
        previous_key = "previous_rank_asc" if ascending else "previous_rank_desc"
        payload = [
            {
                "territory_code": row["territory_code"],
                "name_ru": row["name_ru"],
                "district_code": row.get("district_code"),
                "value": row["value"],
                "rank": row.get(rank_key),
                "previous_rank": row.get(previous_key),
                "percentile": row.get("percentile"),
                "ratio_to_country": row.get("ratio_to_country"),
            }
            for row in rows
        ]

        return Response(
            {
                "series": series_key,
                "year": year,
                "previous_year": previous_year,
                "order": "asc" if ascending else "desc",
                "count": len(payload),
                "results": RankingRowSerializer(payload, many=True).data,
            }
        )


@extend_schema(tags=["Источники"])
class SourceViewSet(viewsets.ReadOnlyModelViewSet):
    """Внешние источники, выпуски которых собирает проект: Росстат, Банк России, ФНС."""

    serializer_class = SourceSerializer
    lookup_field = "code"

    def get_queryset(self) -> QuerySet[Source]:
        """Источники по коду."""
        return Source.objects.order_by("code")


@extend_schema(tags=["Источники"])
class ReleaseListView(generics.ListAPIView):
    """Выпуски источников в архиве проекта, новые первыми."""

    serializer_class = ReleaseSerializer
    filterset_fields = ["source__code", "status", "reference_year"]

    def get_queryset(self) -> QuerySet[Release]:
        """Выпуски с источником."""
        return Release.objects.select_related("source").order_by(
            "-published_on", "-fetched_at", "source__code"
        )


@extend_schema(
    tags=["Наблюдения"],
    parameters=[MonthlyQuerySerializer],
    responses=MonthlyObservationSerializer(many=True),
)
class MonthlyObservationListView(WarehouseMixin, APIView):
    """Значения ряда по месяцам; без территории — по всем субъектам, без округов и страны."""

    pagination_class = ObservationPagination

    def get(self, request: Request) -> Response:
        """Выдать месячные значения порцией со смещением."""
        query = MonthlyQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        parameters = query.validated_data

        paginator = self.pagination_class()
        limit = paginator.get_limit(request)
        offset = paginator.get_offset(request)

        try:
            rows, total = month_observations(
                parameters["series"],
                territory_code=parameters.get("territory"),
                kind=parameters.get("kind"),
                first_year=parameters.get("year_from"),
                last_year=parameters.get("year_to"),
                limit=limit,
                offset=offset,
            )
        except WarehouseNotBuiltError as error:
            return self.warehouse_unavailable(error)

        if not rows and offset == 0:
            raise NotFound(_("По заданным условиям месячных значений нет"))

        return Response(
            {
                "count": total,
                "limit": limit,
                "offset": offset,
                "results": MonthlyObservationSerializer(rows, many=True).data,
            }
        )
