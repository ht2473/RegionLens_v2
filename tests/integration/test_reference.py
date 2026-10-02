"""Проверки целостности справочника территорий."""

from __future__ import annotations

import json

import pytest
from django.conf import settings
from django.core.management import call_command

from apps.catalog.constants import TerritoryLevel, TerritoryType
from apps.catalog.models import Territory, TerritoryAdjacency

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

EXPECTED_REGIONS = 85
EXPECTED_DISTRICTS = 8
EXPECTED_AGGREGATES = 2


@pytest.fixture(scope="module")
def reference_data() -> dict:
    """Содержимое файла справочника."""
    path = settings.REFERENCE_DIR / "territories.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def loaded_reference(db: None) -> None:
    """Справочник, загруженный в базу данных."""
    call_command("seed_reference", verbosity=0)


class TestReferenceFile:
    """Проверки самого файла справочника, не требующие базы данных."""

    def test_composition(self, reference_data: dict) -> None:
        """Состав территорий соответствует охвату набора данных."""
        assert len(reference_data["regions"]) == EXPECTED_REGIONS
        assert len(reference_data["federal_districts"]) == EXPECTED_DISTRICTS
        assert len(reference_data["aggregates"]) == EXPECTED_AGGREGATES

    def test_codes_are_unique(self, reference_data: dict) -> None:
        """Коды территорий уникальны в пределах справочника."""
        codes = [item["code"] for item in reference_data["regions"]]
        assert len(codes) == len(set(codes))

    def test_source_names_are_unique(self, reference_data: dict) -> None:
        """
        Названия в исходных данных уникальны.

        Совпадение названий сделало бы невозможным однозначное сопоставление
        наблюдений с территориями.
        """
        names = [
            item["source_name"]
            for item in [
                *reference_data["regions"],
                *reference_data["aggregates"],
                *reference_data["federal_districts"],
            ]
        ]
        assert len(names) == len(set(names))

    def test_adjacency_is_symmetric(self, reference_data: dict) -> None:
        """
        Матрица соседства симметрична.

        Асимметрия исказит матрицу пространственных весов и, как следствие,
        значения индексов пространственной автокорреляции.
        """
        adjacency = reference_data["adjacency"]
        for code, neighbours in adjacency.items():
            for neighbour in neighbours:
                assert code in adjacency[neighbour], f"Связь {code} → {neighbour} не имеет обратной"

    def test_no_self_adjacency(self, reference_data: dict) -> None:
        """Территория не может быть сопредельна самой себе."""
        for code, neighbours in reference_data["adjacency"].items():
            assert code not in neighbours

    def test_kaliningrad_has_no_domestic_neighbours(self, reference_data: dict) -> None:
        """
        Калининградская область не имеет сухопутных границ с другими субъектами.

        Это не ошибка справочника, а свойство территории, которое необходимо учитывать
        при построении матрицы пространственных весов.
        """
        assert reference_data["adjacency"]["RU-KGD"] == []

    def test_every_region_has_area(self, reference_data: dict) -> None:
        """Площадь указана для всех субъектов: без неё невозможен расчёт плотности."""
        for region in reference_data["regions"]:
            assert region.get("area_km2", 0) > 0, region["code"]


class TestSeedCommand:
    """Проверки загрузки справочника в базу данных."""

    def test_loads_expected_counts(self, loaded_reference: None) -> None:
        """После загрузки в базе присутствуют все территории."""
        assert Territory.objects.regions().count() == EXPECTED_REGIONS
        assert Territory.objects.federal_districts().count() == EXPECTED_DISTRICTS
        assert Territory.objects.country().count() == 1

    def test_aggregates_are_excluded_from_comparison(self, loaded_reference: None) -> None:
        """
        Составные территории не попадают в выборку для сравнения.

        Их включение привело бы к двойному учёту входящих субъектов в рейтингах
        и показателях дифференциации.
        """
        comparable = Territory.objects.comparable()
        assert comparable.count() == EXPECTED_REGIONS
        assert not comparable.filter(is_aggregate=True).exists()

    def test_every_region_belongs_to_district(self, loaded_reference: None) -> None:
        """Каждый субъект отнесён к федеральному округу."""
        orphans = Territory.objects.regions().filter(parent__isnull=True)
        assert not orphans.exists()

    def test_buryatia_and_zabaykalsky_in_far_eastern_district(self, loaded_reference: None) -> None:
        """
        Республика Бурятия и Забайкальский край отнесены к Дальневосточному округу.

        Состав округов — после Указа от 3 ноября 2018 г. № 632.
        """
        for code in ("RU-BU", "RU-ZAB"):
            territory = Territory.objects.get(code=code)
            assert territory.parent.code == "FD-DFO"

    def test_crimea_and_sevastopol_have_start_year(self, loaded_reference: None) -> None:
        """Для Крыма и Севастополя зафиксирован год начала наблюдений."""
        for code in ("RU-CR", "RU-SEV"):
            assert Territory.objects.get(code=code).data_since_year == 2014

    def test_federal_cities_marked_correctly(self, loaded_reference: None) -> None:
        """Города федерального значения выделены отдельным типом."""
        cities = Territory.objects.filter(territory_type=TerritoryType.FEDERAL_CITY)
        assert set(cities.values_list("code", flat=True)) == {"RU-MOW", "RU-SPE", "RU-SEV"}

    def test_adjacency_loaded_in_both_directions(self, loaded_reference: None) -> None:
        """Связи соседства загружены в обе стороны."""
        moscow = Territory.objects.get(code="RU-MOW")
        region = Territory.objects.get(code="RU-MOS")
        assert TerritoryAdjacency.objects.filter(territory=moscow, neighbour=region).exists()
        assert TerritoryAdjacency.objects.filter(territory=region, neighbour=moscow).exists()

    def test_command_is_idempotent(self, loaded_reference: None) -> None:
        """
        Повторный запуск не создаёт дубликатов.

        Команда выполняется при каждом развёртывании, поэтому идемпотентность
        является обязательным свойством.
        """
        before = Territory.objects.count()
        call_command("seed_reference", verbosity=0)
        assert Territory.objects.count() == before

    def test_levels_are_assigned(self, loaded_reference: None) -> None:
        """Уровень территории заполнен у всех записей."""
        assert not Territory.objects.filter(level="").exists()
        assert Territory.objects.filter(level=TerritoryLevel.COUNTRY).count() == 1
