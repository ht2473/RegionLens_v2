"""
Загрузка справочника территорий из ``data/reference/territories.json`` с проверкой.

Координаты и плитки — из необязательного ``geography.json`` (``build_boundaries``).
"""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.constants import TerritoryLevel, TerritoryType
from apps.catalog.models import Territory, TerritoryAdjacency
from apps.core.utils.text import make_slug

# Ожидаемое число субъектов Российской Федерации в наборе данных.
EXPECTED_REGION_COUNT = 85
EXPECTED_DISTRICT_COUNT = 8


class Command(BaseCommand):
    """Загрузить и проверить справочник территориальных единиц."""

    help = "Загружает справочник территорий и матрицу соседства из data/reference/territories.json"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument(
            "--check",
            action="store_true",
            help="Только проверить целостность справочника, не изменяя базу данных",
        )
        parser.add_argument(
            "--path",
            type=Path,
            default=None,
            help="Альтернативный путь к файлу справочника",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Точка входа команды."""
        path: Path = options["path"] or (settings.REFERENCE_DIR / "territories.json")
        if not path.exists():
            raise CommandError(f"Файл справочника не найден: {path}")

        data = json.loads(path.read_text(encoding="utf-8"))
        self.stdout.write(f"Справочник: {path}")

        problems = self._validate(data)
        if problems:
            for problem in problems:
                self.stderr.write(self.style.ERROR(f"  ✗ {problem}"))
            raise CommandError(f"Справочник не прошёл проверку: замечаний — {len(problems)}")

        self.stdout.write(self.style.SUCCESS("  ✓ Справочник корректен"))

        if options["check"]:
            self.stdout.write("Режим проверки: база данных не изменялась")
            return

        with transaction.atomic():
            stats = self._load(data)

        self.stdout.write(
            self.style.SUCCESS(
                "Загружено: страна — {country}, округов — {districts}, "
                "субъектов — {regions}, составных территорий — {aggregates}, "
                "связей соседства — {adjacency}, "
                "географических характеристик — {geography}".format(**stats)
            )
        )

    # -----------------------------------------------------------------------------------
    # Проверка целостности
    # -----------------------------------------------------------------------------------

    def _validate(  # noqa: PLR0912 - последовательный перечень независимых проверок
        self, data: dict[str, Any]
    ) -> list[str]:
        """Проверить справочник и вернуть список обнаруженных замечаний."""
        problems: list[str] = []

        districts = {item["code"] for item in data["federal_districts"]}
        regions = {item["code"] for item in data["regions"]}

        if len(data["regions"]) != EXPECTED_REGION_COUNT:
            problems.append(
                f"Субъектов в справочнике {len(data['regions'])}, ожидается {EXPECTED_REGION_COUNT}"
            )
        if len(districts) != EXPECTED_DISTRICT_COUNT:
            problems.append(
                f"Федеральных округов {len(districts)}, ожидается {EXPECTED_DISTRICT_COUNT}"
            )

        # Уникальность кодов и названий в исходных данных.
        source_names: dict[str, str] = {}
        for item in [*data["regions"], *data["aggregates"], *data["federal_districts"]]:
            name = item["source_name"]
            if name in source_names:
                problems.append(f"Название «{name}» встречается дважды")
            source_names[name] = item["code"]

        # Каждый субъект отнесён к существующему округу.
        for region in data["regions"]:
            if region["district"] not in districts:
                problems.append(
                    f"{region['code']}: неизвестный федеральный округ {region['district']}"
                )

        # Матрица соседства: ссылки на существующие коды и симметричность.
        adjacency: dict[str, list[str]] = data["adjacency"]
        missing_in_adjacency = regions - set(adjacency)
        if missing_in_adjacency:
            problems.append(
                "Отсутствуют в матрице соседства: " + ", ".join(sorted(missing_in_adjacency))
            )

        for code, neighbours in adjacency.items():
            if code not in regions:
                problems.append(f"Матрица соседства ссылается на неизвестный код {code}")
                continue
            for neighbour in neighbours:
                if neighbour not in regions:
                    problems.append(f"{code}: неизвестный сосед {neighbour}")
                elif code not in adjacency.get(neighbour, []):
                    problems.append(
                        f"Нарушена симметричность: {code} → {neighbour}, обратной связи нет"
                    )
                if neighbour == code:
                    problems.append(f"{code}: территория указана соседом самой себе")

        # Составные территории ссылаются на существующие субъекты.
        for aggregate in data["aggregates"]:
            for component in aggregate["components"]:
                if component not in regions:
                    problems.append(f"{aggregate['code']}: неизвестный компонент {component}")

        return problems

    # -----------------------------------------------------------------------------------
    # Загрузка
    # -----------------------------------------------------------------------------------

    def _load(self, data: dict[str, Any]) -> dict[str, int]:
        """Записать справочник в базу данных и вернуть статистику."""
        country = self._upsert_country(data["country"])
        districts = self._upsert_districts(data["federal_districts"], country)
        regions = self._upsert_regions(data["regions"], districts)
        aggregates = self._upsert_aggregates(data["aggregates"], districts)
        adjacency_count = self._upsert_adjacency(
            data["adjacency"], data.get("adjacency_special", []), regions
        )
        geography_count = self._upsert_geography(regions)

        return {
            "country": 1 if country else 0,
            "districts": len(districts),
            "regions": len(regions),
            "aggregates": len(aggregates),
            "adjacency": adjacency_count,
            "geography": geography_count,
        }

    def _upsert_geography(self, regions: dict[str, Territory]) -> int:
        """Записать координаты центров и позиции плиточной картограммы."""
        path = settings.REFERENCE_DIR / "geography.json"
        if not path.exists():
            self.stdout.write(
                self.style.WARNING(
                    "  ! Файл geography.json не найден — плиточная карта будет недоступна. "
                    "Выполните: python manage.py build_boundaries"
                )
            )
            return 0

        payload = json.loads(path.read_text(encoding="utf-8"))
        updated: list[Territory] = []

        for code, item in payload.get("regions", {}).items():
            territory = regions.get(code)
            if territory is None:
                continue
            territory.latitude = item.get("latitude")
            territory.longitude = item.get("longitude")
            territory.tile_x = item.get("tile_x")
            territory.tile_y = item.get("tile_y")
            updated.append(territory)

        Territory.objects.bulk_update(
            updated, ["latitude", "longitude", "tile_x", "tile_y"], batch_size=100
        )
        return len(updated)

    def _upsert_country(self, item: dict[str, Any]) -> Territory:
        """Создать или обновить запись о стране в целом."""
        territory, _ = Territory.objects.update_or_create(
            code=item["code"],
            defaults={
                "slug": make_slug(item["name_ru"]),
                "source_name": item["source_name"],
                "name_ru": item["name_ru"],
                "name_en": item["name_en"],
                "abbreviation": item["abbr"],
                "level": TerritoryLevel.COUNTRY,
                "territory_type": TerritoryType.NOT_APPLICABLE,
                "iso_code": item.get("iso_code", ""),
                "okato": item.get("okato", ""),
                "capital_ru": item.get("capital_ru", ""),
                "capital_en": item.get("capital_en", ""),
                "area_km2": item.get("area_km2"),
                "display_order": 0,
                "parent": None,
            },
        )
        return territory

    def _upsert_districts(
        self, items: list[dict[str, Any]], country: Territory
    ) -> dict[str, Territory]:
        """Создать или обновить федеральные округа."""
        districts: dict[str, Territory] = {}
        for item in items:
            territory, _ = Territory.objects.update_or_create(
                code=item["code"],
                defaults={
                    "slug": make_slug(item["name_ru"]),
                    "source_name": item["source_name"],
                    "name_ru": item["name_ru"],
                    "name_en": item["name_en"],
                    "abbreviation": item["abbr"],
                    "level": TerritoryLevel.FEDERAL_DISTRICT,
                    "territory_type": TerritoryType.NOT_APPLICABLE,
                    "capital_ru": item.get("capital_ru", ""),
                    "capital_en": item.get("capital_en", ""),
                    "display_order": item.get("display_order", 100),
                    "parent": country,
                },
            )
            districts[item["code"]] = territory
        return districts

    def _upsert_regions(
        self, items: list[dict[str, Any]], districts: dict[str, Territory]
    ) -> dict[str, Territory]:
        """Создать или обновить субъекты Российской Федерации."""
        regions: dict[str, Territory] = {}
        for order, item in enumerate(items, start=1):
            territory, _ = Territory.objects.update_or_create(
                code=item["code"],
                defaults={
                    "slug": make_slug(item["name_ru"]),
                    "source_name": item["source_name"],
                    "name_ru": item["name_ru"],
                    "name_en": item["name_en"],
                    "abbreviation": item["abbr"],
                    "level": TerritoryLevel.REGION,
                    "territory_type": item["type"],
                    "iso_code": item["code"],
                    "okato": item.get("okato", ""),
                    "capital_ru": item.get("capital_ru", ""),
                    "capital_en": item.get("capital_en", ""),
                    "area_km2": item.get("area_km2"),
                    "utc_offset": item.get("utc_offset"),
                    "data_since_year": item.get("data_since_year"),
                    "note_ru": item.get("note_ru", ""),
                    "note_en": item.get("note_en", ""),
                    "is_aggregate": False,
                    "display_order": order,
                    "parent": districts[item["district"]],
                },
            )
            regions[item["code"]] = territory
        return regions

    def _upsert_aggregates(
        self, items: list[dict[str, Any]], districts: dict[str, Territory]
    ) -> dict[str, Territory]:
        """Создать или обновить составные территории."""
        aggregates: dict[str, Territory] = {}
        for item in items:
            territory, _ = Territory.objects.update_or_create(
                code=item["code"],
                defaults={
                    "slug": make_slug(item["name_ru"]),
                    "source_name": item["source_name"],
                    "name_ru": item["name_ru"],
                    "name_en": item["name_en"],
                    "abbreviation": item["abbr"],
                    "level": TerritoryLevel.REGION,
                    "territory_type": TerritoryType.AGGREGATE,
                    "note_ru": item.get("note_ru", ""),
                    "note_en": item.get("note_en", ""),
                    "is_aggregate": True,
                    "display_order": 900,
                    "parent": districts[item["district"]],
                },
            )
            aggregates[item["code"]] = territory
        return aggregates

    def _upsert_adjacency(
        self,
        adjacency: dict[str, list[str]],
        special: list[dict[str, Any]],
        regions: dict[str, Territory],
    ) -> int:
        """Перестроить матрицу соседства целиком: связей около четырёхсот."""
        TerritoryAdjacency.objects.all().delete()

        links: list[TerritoryAdjacency] = [
            TerritoryAdjacency(
                territory=regions[code],
                neighbour=regions[neighbour],
                kind=TerritoryAdjacency.Kind.LAND,
            )
            for code, neighbours in adjacency.items()
            for neighbour in neighbours
        ]

        # Морские и мостовые связи хранятся отдельным списком и добавляются в обе стороны.
        for item in special:
            first, second = item["pair"]
            kind = item["kind"]
            links.append(
                TerritoryAdjacency(territory=regions[first], neighbour=regions[second], kind=kind)
            )
            links.append(
                TerritoryAdjacency(territory=regions[second], neighbour=regions[first], kind=kind)
            )

        TerritoryAdjacency.objects.bulk_create(links, ignore_conflicts=True)
        return TerritoryAdjacency.objects.count()
