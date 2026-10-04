"""
Представление данных в ответах API.

Поля названы по предметной области, внутренние ключи наружу не отдаются; названия —
на обоих языках.
"""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.catalog.constants import ValueFlag, ValueQuality
from apps.catalog.models import Indicator, Series, Territory
from apps.sources.models import Release, Source
from apps.warehouse.routing import is_user_key

# Признак качества наблюдения словом.
QUALITY_NAMES: dict[int, str] = {
    ValueQuality.OBSERVED: "observed",
    ValueQuality.NO_DATA: "no_data",
    ValueQuality.HIDDEN: "hidden",
    ValueQuality.NOT_APPLICABLE: "not_applicable",
}
# Признаки значения словами.
FLAG_NAMES: dict[ValueFlag, str] = {
    ValueFlag.PRELIMINARY: "preliminary",
    ValueFlag.COMPUTED: "computed",
    ValueFlag.FROZEN_DENOMINATOR: "frozen_denominator",
}
# Источник значений набора «Если быть точным».
DATASET_SOURCE = "dataset"


def flag_names(flags: Any) -> list[str]:
    """Признаки значения из битов склада словами."""
    value = ValueFlag(int(flags or 0))
    return [name for flag, name in FLAG_NAMES.items() if flag in value]


class TerritorySerializer(serializers.ModelSerializer):
    """Территория: субъект, федеральный округ или страна."""

    district = serializers.SlugRelatedField(source="parent", slug_field="code", read_only=True)

    class Meta:
        model = Territory
        fields = [
            "code",
            "slug",
            "name_ru",
            "name_en",
            "abbreviation",
            "level",
            "territory_type",
            "district",
            "is_aggregate",
            "iso_code",
            "okato",
            "capital_ru",
            "capital_en",
            "latitude",
            "longitude",
            "utc_offset",
        ]


class SeriesBriefSerializer(serializers.ModelSerializer):
    """Краткое описание ряда наблюдений внутри карточки показателя."""

    unit = serializers.CharField(source="unit.name_ru", read_only=True, default="")

    class Meta:
        model = Series
        fields = [
            "key",
            "name_ru",
            "name_en",
            "unit",
            "first_year",
            "last_year",
            "region_coverage",
            "completeness",
            "is_analysis_ready",
        ]


class IndicatorSerializer(serializers.ModelSerializer):
    """Показатель с перечнем его рядов."""

    section = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    series = SeriesBriefSerializer(many=True, read_only=True)

    class Meta:
        model = Indicator
        fields = [
            "code",
            "slug",
            "name_ru",
            "name_en",
            "description_ru",
            "section",
            "polarity",
            "is_featured",
            "series_count",
            "observation_count",
            "first_year",
            "last_year",
            "series",
        ]


class SeriesSerializer(serializers.ModelSerializer):
    """Ряд наблюдений — единица анализа системы."""

    indicator = serializers.SlugRelatedField(slug_field="code", read_only=True)
    indicator_name_ru = serializers.CharField(source="indicator.name_ru", read_only=True)
    section = serializers.SlugRelatedField(
        source="indicator.section", slug_field="slug", read_only=True
    )
    unit = serializers.CharField(source="unit.name_ru", read_only=True, default="")
    unit_short = serializers.CharField(source="unit.short_name_ru", read_only=True, default="")

    class Meta:
        model = Series
        fields = [
            "key",
            "slug",
            "indicator",
            "indicator_name_ru",
            "section",
            "name_ru",
            "name_en",
            "has_subsection",
            "unit",
            "unit_short",
            "polarity",
            "first_year",
            "last_year",
            "year_count",
            "region_count",
            "region_coverage",
            "completeness",
            "is_analysis_ready",
            "break_count",
            "revision_count",
        ]


class ObservationSerializer(serializers.Serializer):
    """Одно наблюдение: значение ряда для территории за год с признаком качества."""

    series_key = serializers.CharField()
    territory_code = serializers.CharField()
    year = serializers.IntegerField()
    value = serializers.FloatField(allow_null=True)
    quality = serializers.SerializerMethodField(
        help_text="observed, no_data, hidden или not_applicable",
    )
    source = serializers.SerializerMethodField(
        help_text="dataset — набор «Если быть точным»; иначе код источника (sources)",
    )
    release = serializers.SerializerMethodField(
        help_text="Выпуск, из которого взято значение: год сборника или код выпуска источника",
    )
    flags = serializers.SerializerMethodField(
        help_text="Признаки значения: preliminary, computed, frozen_denominator",
    )

    def get_quality(self, obj: dict[str, Any]) -> str:
        """Назвать признак качества словом, пригодным для разбора программой."""
        quality = obj.get("quality")
        if quality is None:
            return "unknown"
        try:
            return QUALITY_NAMES[int(quality)]
        except TypeError, ValueError, KeyError:
            return "unknown"

    def get_source(self, obj: dict[str, Any]) -> str:
        """Источник значения: набор или модуль внешнего источника."""
        return obj.get("source_code") or DATASET_SOURCE

    def get_release(self, obj: dict[str, Any]) -> str | None:
        """Выпуск, из которого взято значение."""
        return obj.get("edition_label")

    def get_flags(self, obj: dict[str, Any]) -> list[str]:
        """Признаки значения словами."""
        return flag_names(obj.get("flags"))

    def create(self, validated_data: dict[str, Any]) -> Any:  # pragma: no cover - только чтение
        """Интерфейс работает только на чтение."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:  # pragma: no cover
        """Интерфейс работает только на чтение."""
        raise NotImplementedError


class RankingRowSerializer(serializers.Serializer):
    """Строка рейтинга субъектов за год."""

    territory_code = serializers.CharField()
    name_ru = serializers.CharField()
    district_code = serializers.CharField(allow_null=True, required=False)
    value = serializers.FloatField(allow_null=True)
    rank = serializers.IntegerField(allow_null=True)
    previous_rank = serializers.IntegerField(allow_null=True, required=False)
    percentile = serializers.FloatField(allow_null=True, required=False)
    ratio_to_country = serializers.FloatField(allow_null=True, required=False)

    def create(self, validated_data: dict[str, Any]) -> Any:  # pragma: no cover - только чтение
        """Интерфейс работает только на чтение."""
        raise NotImplementedError

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:  # pragma: no cover
        """Интерфейс работает только на чтение."""
        raise NotImplementedError


def open_series_key(value: str) -> str:
    """Ключ ряда открытых данных: ряды таблиц пользователей интерфейс не отдаёт."""
    if is_user_key(value):
        raise serializers.ValidationError(
            _("Интерфейс отдаёт только открытые данные сайта; ряды своих таблиц в нём недоступны.")
        )
    return value


class ObservationQuerySerializer(serializers.Serializer):
    """Параметры запроса наблюдений."""

    series = serializers.CharField(
        help_text="Ключ ряда наблюдений, например Y477110378:00", validators=[open_series_key]
    )
    territory = serializers.CharField(
        required=False,
        help_text="Код территории; если не задан, возвращаются все субъекты",
    )
    year_from = serializers.IntegerField(required=False, min_value=1990, max_value=2100)
    year_to = serializers.IntegerField(required=False, min_value=1990, max_value=2100)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Проверить, что границы периода не переставлены местами."""
        first, last = attrs.get("year_from"), attrs.get("year_to")
        if first and last and first > last:
            raise serializers.ValidationError(
                {"year_from": "Начало периода не может быть позже его конца"}
            )
        return attrs


class RankingQuerySerializer(serializers.Serializer):
    """Параметры запроса рейтинга."""

    series = serializers.CharField(help_text="Ключ ряда наблюдений", validators=[open_series_key])
    year = serializers.IntegerField(
        required=False,
        min_value=1990,
        max_value=2100,
        help_text="Год рейтинга; по умолчанию последний доступный",
    )
    order = serializers.ChoiceField(
        choices=["desc", "asc"],
        required=False,
        default="desc",
        help_text="desc — от больших значений к меньшим, asc — наоборот",
    )


class SourceSerializer(serializers.ModelSerializer):
    """Внешний источник: издатель, условия использования, последняя проверка и выпуск."""

    latest_release = serializers.SerializerMethodField(
        help_text="Код последнего разобранного выпуска"
    )

    class Meta:
        model = Source
        fields = [
            "code",
            "title_ru",
            "title_en",
            "publisher_ru",
            "publisher_en",
            "licence_ru",
            "licence_en",
            "page_url",
            "checked_at",
            "check_ok",
            "latest_release",
        ]

    def get_latest_release(self, obj: Source) -> str | None:
        """Последний разобранный выпуск."""
        release = obj.latest_release()
        return release.code if release is not None else None


class ReleaseSerializer(serializers.ModelSerializer):
    """Выпуск источника в архиве: откуда получен, контрольная сумма и итог разбора."""

    source = serializers.SlugRelatedField(slug_field="code", read_only=True)

    class Meta:
        model = Release
        fields = [
            "source",
            "code",
            "title",
            "reference_year",
            "published_on",
            "fetched_at",
            "url",
            "sha256",
            "size_bytes",
            "status",
            "parsed_at",
            "row_count",
        ]


class MonthlyObservationSerializer(ObservationSerializer):
    """Месячное значение: ряд, территория, год, месяц и вид значения."""

    month = serializers.IntegerField()
    kind = serializers.CharField(
        help_text="level — за месяц или на его конец, ytd — с начала года, "
        "yoy — к тому же периоду прошлого года"
    )


class MonthlyQuerySerializer(ObservationQuerySerializer):
    """Параметры запроса месячных значений."""

    kind = serializers.ChoiceField(
        choices=["level", "ytd", "yoy"],
        required=False,
        help_text="Вид значения; без него — все виды",
    )
