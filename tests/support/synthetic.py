"""
Синтетический исходный набор для проверок — уменьшенная копия источника с его структурой.

Те же атрибуты и типы, уровни и названия территорий, заглушки пропусков, ряды разного
покрытия, два выпуска с пересмотрами, примечание с разрывом, обратная полярность и единица
с укрупнением для страны. Значения детерминированы фиксированным зерном.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from apps.catalog.constants import SENTINEL_HIDDEN, SENTINEL_NO_DATA

# Справочник территорий — единственный источник истины о территориальной структуре,
# поэтому синтетический набор строится по нему, а не по собственному перечню.
REFERENCE_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "territories.json"

# Период набора совпадает с реальным: часть расчётов опирается на длину ряда.
FIRST_YEAR = 2001
LAST_YEAR = 2025

# Последний год источника заполнен частично — так же, как в реальном наборе.
PRELIMINARY_YEAR = LAST_YEAR

# Выпуски изданий. Более поздний выпуск побеждает при разрешении конфликтов.
EDITION_CURRENT = "Регионы России. Социально-экономические показатели 2025"
EDITION_PREVIOUS = "Регионы России. Социально-экономические показатели 2022"

# Год, до которого включительно значения присутствуют в раннем выпуске.
REVISION_LAST_YEAR = 2020

# Версия набора данных: код используется как идентификатор версии в каталоге.
VERSION_CODE = "v20260313"

# Зерно генератора. Изменение зерна меняет все ожидаемые величины в тестах.
RANDOM_SEED = 20260321

# Число субъектов и лет в ряду с недостаточным покрытием.
PARTIAL_REGIONS = 20
PARTIAL_YEARS = 5


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    """Описание одного показателя синтетического набора."""

    code: str
    section: str
    indicator: str
    unit: str
    # Разрезы показателя. Заглушка "CD" означает, что разрезов нет.
    subsections: tuple[str, ...] = ("CD",)
    # Значение типичного субъекта в первый год ряда.
    base: float = 100.0
    # Разброс между субъектами: доля от базового значения.
    spread: float = 0.30
    # Среднегодовой темп прироста.
    growth: float = 0.02
    # Способ получения значения округа и страны: сумма субъектов или среднее.
    aggregation: str = "sum"
    # Методическое примечание источника.
    comment: str | None = None
    # Покрытие: full — все субъекты и годы, partial — двадцать субъектов за пять лет,
    # thin_last — все субъекты, но в последнем году только двадцать.
    coverage: str = "full"
    # Показатель публикуется в двух выпусках с расходящимися значениями.
    revised: bool = False
    # Число ячеек со скрытым значением.
    hidden_cells: int = 0
    # Число ячеек, для которых данные отсутствуют.
    missing_cells: int = 0
    # Показатель принимает отрицательные значения; остальные строго положительны.
    allow_negative: bool = False


# ---------------------------------------------------------------------------------------
# Состав набора: коды показателей — из настоящего набора, на них опирается
# data/reference/featured_series.json. Шестнадцать показателей в пяти разделах.
# ---------------------------------------------------------------------------------------

# Показатели, на которые ссылаются тесты по смыслу, а не по порядковому номеру.
POPULATION_CODE = "Y477110461"
LIFE_EXPECTANCY_CODE = "Y477110256"
GRP_PER_CAPITA_CODE = "Y477110006"
GRP_TOTAL_CODE = "Y477110005"
WAGE_CODE = "Y477110378"
UNEMPLOYMENT_CODE = "Y477110418"
NATURAL_GROWTH_CODE = "Y477110901"
LABOUR_FORCE_CODE = "Y477110900"
PARTIAL_COVERAGE_CODE = "Y477110903"
INCOME_CODE = "Y477110374"
CPI_CODE = "Y477110111"
REAL_WAGE_CODE = "Y477110362"
BASKET_CODE = "Y477110395"
THIN_LAST_CODE = "Y477110904"

SERIES_SPECS: tuple[SeriesSpec, ...] = (
    SeriesSpec(
        code=POPULATION_CODE,
        section="Население",
        indicator="Численность населения",
        unit="Тысяч человек",
        base=1_600.0,
        spread=0.75,
        growth=-0.002,
        aggregation="sum",
    ),
    SeriesSpec(
        code=LIFE_EXPECTANCY_CODE,
        section="Население",
        indicator="Ожидаемая продолжительность жизни при рождении",
        unit="Лет",
        base=70.0,
        spread=0.06,
        growth=0.004,
        aggregation="mean",
        comment=(
            "С 2017 года показатель рассчитывается по уточнённой методике "
            "с учётом итогов Всероссийской переписи населения, поэтому данные "
            "за предшествующие годы несопоставимы с последующими"
        ),
    ),
    SeriesSpec(
        code=NATURAL_GROWTH_CODE,
        section="Население",
        indicator="Коэффициент естественного прироста населения",
        unit="На 1000 человек населения",
        base=-1.5,
        spread=2.20,
        growth=0.0,
        aggregation="mean",
        missing_cells=12,
        allow_negative=True,
    ),
    SeriesSpec(
        code="Y477110902",
        section="Население",
        indicator="Число зарегистрированных преступлений",
        unit="Единиц",
        base=21_000.0,
        spread=0.65,
        growth=-0.030,
        aggregation="sum",
    ),
    SeriesSpec(
        code=UNEMPLOYMENT_CODE,
        section="Труд и занятость",
        indicator="Уровень безработицы",
        unit="Процентов",
        base=8.5,
        spread=0.45,
        growth=-0.020,
        aggregation="mean",
    ),
    SeriesSpec(
        code=WAGE_CODE,
        section="Уровень жизни",
        indicator="Среднемесячная номинальная начисленная заработная плата работников",
        unit="Рублей",
        base=4_200.0,
        spread=0.40,
        growth=0.115,
        aggregation="mean",
        revised=True,
    ),
    SeriesSpec(
        code="Y477110374",
        section="Уровень жизни",
        indicator="Среднедушевые денежные доходы населения",
        unit="Рублей",
        base=3_500.0,
        spread=0.38,
        growth=0.110,
        aggregation="mean",
    ),
    SeriesSpec(
        code="Y477110463",
        section="Уровень жизни",
        indicator="Численность населения с денежными доходами ниже границы бедности",
        unit="Процентов",
        base=22.0,
        spread=0.35,
        growth=-0.030,
        aggregation="mean",
    ),
    SeriesSpec(
        code=LABOUR_FORCE_CODE,
        section="Труд и занятость",
        indicator="Численность рабочей силы",
        unit="Тысяч человек",
        subsections=("Мужчины", "Женщины"),
        base=420.0,
        spread=0.70,
        growth=0.003,
        aggregation="sum",
        hidden_cells=8,
    ),
    SeriesSpec(
        code=GRP_TOTAL_CODE,
        section="Экономика",
        indicator="Валовой региональный продукт",
        # Единица с укрупнением для страны в целом: проверяет приведение значения
        # по России к единице измерения субъектов.
        unit="Миллионов рублей; для значений в целом по России: млрд руб",
        base=52_000.0,
        spread=0.85,
        growth=0.125,
        aggregation="sum",
        revised=True,
    ),
    SeriesSpec(
        code=GRP_PER_CAPITA_CODE,
        section="Экономика",
        indicator="Валовой региональный продукт на душу населения",
        unit="Рублей",
        base=90_000.0,
        spread=0.50,
        growth=0.115,
        aggregation="mean",
    ),
    SeriesSpec(
        code="Y477110108",
        section="Экономика",
        indicator="Инвестиции в основной капитал на душу населения",
        unit="Рублей",
        base=12_000.0,
        spread=0.60,
        growth=0.100,
        aggregation="mean",
    ),
    SeriesSpec(
        code="Y477110224",
        section="Экономика",
        indicator="Оборот розничной торговли на душу населения",
        unit="Рублей",
        base=18_000.0,
        spread=0.45,
        growth=0.105,
        aggregation="mean",
    ),
    SeriesSpec(
        code="Y477110017",
        section="Жилищные условия",
        indicator="Ввод в действие жилых домов на 1000 человек населения",
        unit="Квадратных метров общей площади",
        base=250.0,
        spread=0.45,
        growth=0.030,
        aggregation="mean",
    ),
    SeriesSpec(
        code="Y477110226",
        section="Жилищные условия",
        indicator="Общая площадь жилых помещений, приходящаяся в среднем на одного жителя",
        unit="Квадратных метров",
        base=20.0,
        spread=0.12,
        growth=0.015,
        aggregation="mean",
    ),
    # Показатель с недостаточным покрытием: его ряд не должен предлагаться
    # в инструментах сравнительного анализа.
    SeriesSpec(
        code=PARTIAL_COVERAGE_CODE,
        section="Экономика",
        indicator="Объём платных услуг цифровых платформ",
        unit="Миллионов рублей",
        base=900.0,
        spread=0.80,
        growth=0.180,
        aggregation="sum",
        coverage="partial",
    ),
    # Индексы для пересчёта денежных рядов в реальное выражение и стоимость набора.
    SeriesSpec(
        code=CPI_CODE,
        section="Цены",
        indicator="Индексы потребительских цен",
        unit="Процентов",
        base=106.0,
        spread=0.02,
        growth=0.0,
        aggregation="mean",
    ),
    SeriesSpec(
        code=REAL_WAGE_CODE,
        section="Уровень жизни",
        indicator="Реальная начисленная заработная плата работников организаций",
        unit="В процентах к предыдущему году",
        base=104.0,
        spread=0.02,
        growth=0.0,
        aggregation="mean",
    ),
    SeriesSpec(
        code=BASKET_CODE,
        section="Цены",
        indicator="Стоимость фиксированного набора потребительских товаров и услуг",
        unit="Рублей",
        base=2_500.0,
        spread=0.20,
        growth=0.075,
        aggregation="mean",
    ),
    # Последний год заполнен по двадцати субъектам: год по умолчанию — предпоследний.
    SeriesSpec(
        code=THIN_LAST_CODE,
        section="Уровень жизни",
        indicator="Оборот общественного питания на душу населения",
        unit="Рублей",
        base=1_500.0,
        spread=0.40,
        growth=0.100,
        aggregation="mean",
        coverage="thin_last",
    ),
)

# Число разделов, которые получит каталог после сборки склада.
SECTION_COUNT = len({spec.section for spec in SERIES_SPECS})


@dataclass(slots=True)
class SyntheticDataset:
    """Сведения о построенном наборе, нужные тестам."""

    path: Path
    first_year: int
    last_year: int
    row_count: int
    series_count: int
    region_count: int
    # Коды показателей, ряды которых пригодны к сравнительному анализу.
    analysis_ready_codes: tuple[str, ...] = field(default_factory=tuple)
    # Код показателя с недостаточным покрытием.
    partial_code: str = ""
    # Код показателя, значения которого пересматривались между выпусками.
    revised_code: str = ""
    # Код показателя с методическим примечанием о смене методики.
    break_code: str = ""


@dataclass(frozen=True, slots=True)
class Territory:
    """Территория синтетического набора."""

    code: str
    source_name: str
    level: str
    district: str | None
    okato: str
    since: int | None
    components: tuple[str, ...] = ()


def load_territories() -> list[Territory]:
    """Прочитать состав территорий из справочника проекта."""
    payload = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))

    territories: list[Territory] = [
        Territory(
            code=payload["country"]["code"],
            source_name=payload["country"]["source_name"],
            level="country",
            district=None,
            okato=payload["country"].get("okato", "00000000"),
            since=None,
        )
    ]
    territories += [
        Territory(
            code=item["code"],
            source_name=item["source_name"],
            level="federal_district",
            district=None,
            okato="00000000",
            since=None,
        )
        for item in payload["federal_districts"]
    ]
    territories += [
        Territory(
            code=item["code"],
            source_name=item["source_name"],
            level="region",
            district=item["district"],
            okato=item.get("okato", "00000000"),
            since=item.get("data_since_year"),
        )
        for item in payload["regions"]
    ]
    territories += [
        Territory(
            code=item["code"],
            source_name=item["source_name"],
            level="region",
            district=item["district"],
            okato=item.get("okato", "00000000"),
            since=item.get("data_since_year"),
            components=tuple(item["components"]),
        )
        for item in payload["aggregates"]
    ]
    return territories


def build_source_file(path: Path) -> SyntheticDataset:
    """
    Построить синтетический файл parquet по структуре исходного набора.

    :param path: путь создаваемого файла.
    :return: сведения о наборе для использования в тестах.
    """
    territories = load_territories()
    regions = [item for item in territories if item.level == "region" and not item.components]
    aggregates = [item for item in territories if item.components]

    years = list(range(FIRST_YEAR, LAST_YEAR + 1))
    rng = np.random.default_rng(RANDOM_SEED)
    rows: list[dict[str, object]] = []

    for spec in SERIES_SPECS:
        for subsection in spec.subsections:
            values = _series_values(spec, subsection, territories, years, rng)
            rows.extend(_series_rows(spec, subsection, territories, years, values, rng))

    frame = pd.DataFrame(rows, columns=list(COLUMNS))
    frame = frame.astype({"year": "int32", "indicator_value": "float64"})

    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, engine="pyarrow", index=False)

    return SyntheticDataset(
        path=path,
        first_year=FIRST_YEAR,
        last_year=LAST_YEAR,
        row_count=len(frame),
        series_count=sum(len(spec.subsections) for spec in SERIES_SPECS),
        region_count=len(regions) + len(aggregates),
        analysis_ready_codes=tuple(
            spec.code for spec in SERIES_SPECS if spec.coverage in ("full", "thin_last")
        ),
        partial_code=next(spec.code for spec in SERIES_SPECS if spec.coverage == "partial"),
        revised_code=next(spec.code for spec in SERIES_SPECS if spec.revised),
        break_code=next(spec.code for spec in SERIES_SPECS if spec.comment),
    )


# ---------------------------------------------------------------------------------------
# Порождение значений
# ---------------------------------------------------------------------------------------

# Порядок атрибутов повторяет исходный набор.
COLUMNS = (
    "section",
    "indicator_code",
    "indicator_name",
    "subsection",
    "object_name",
    "object_level",
    "object_oktmo",
    "object_okato",
    "year",
    "indicator_value",
    "indicator_unit",
    "comment",
    "source",
    "version_date",
)

# Уровни территорий в терминах источника.
SOURCE_LEVELS = {
    "country": "Страна",
    "federal_district": "Федеральный округ",
    "region": "Регион",
}


def _series_values(
    spec: SeriesSpec,
    subsection: str,
    territories: list[Territory],
    years: list[int],
    rng: np.random.Generator,
) -> dict[tuple[str, int], float]:
    """
    Рассчитать значения ряда по субъектам.

    Общий для округа сдвиг создаёт пространственную структуру для индекса Морана.
    """
    regions = [item for item in territories if item.level == "region" and not item.components]
    districts = [item for item in territories if item.level == "federal_district"]

    district_shift = {item.code: rng.normal(0, 0.45) for item in districts}
    # Разрез сдвигает уровень ряда: разные разрезы одного показателя не должны
    # совпадать значение в значение.
    subsection_shift = 1.0 + 0.05 * (len(subsection) % 5 - 2)

    values: dict[tuple[str, int], float] = {}
    for region in regions:
        deviation = district_shift.get(region.district or "", 0.0) + rng.normal(0, 0.35)
        if spec.allow_negative:
            level = spec.base * subsection_shift + spec.spread * deviation * abs(spec.base)
        else:
            # Мультипликативная форма: правосторонняя асимметрия без отрицательных значений.
            level = spec.base * subsection_shift * float(np.exp(spec.spread * deviation))
        for index, year in enumerate(years):
            noise = rng.normal(0, 0.02)
            value = level * (1 + spec.growth) ** index * (1 + noise)
            values[(region.code, year)] = round(float(value), 3)
    return values


def _aggregate(
    spec: SeriesSpec,
    codes: list[str],
    year: int,
    values: dict[tuple[str, int], float],
) -> float | None:
    """Свернуть значения субъектов в значение округа, страны или составной территории."""
    numbers = [values[(code, year)] for code in codes if (code, year) in values]
    if not numbers:
        return None
    total = float(sum(numbers))
    if spec.aggregation == "mean":
        return round(total / len(numbers), 3)
    return round(total, 3)


def _series_rows(
    spec: SeriesSpec,
    subsection: str,
    territories: list[Territory],
    years: list[int],
    values: dict[tuple[str, int], float],
    rng: np.random.Generator,
) -> list[dict[str, object]]:
    """Развернуть значения ряда в строки исходного формата."""
    regions = [item for item in territories if item.level == "region" and not item.components]
    aggregates = [item for item in territories if item.components]
    districts = [item for item in territories if item.level == "federal_district"]
    country = next(item for item in territories if item.level == "country")

    region_codes = [item.code for item in regions]

    # Ограниченное покрытие: часть субъектов и лет отсутствует полностью.
    if spec.coverage == "partial":
        allowed_regions = set(region_codes[:PARTIAL_REGIONS])
        allowed_years = set(years[-PARTIAL_YEARS:])
    else:
        allowed_regions = set(region_codes)
        allowed_years = set(years)

    # Неполный последний год: значения только у первых двадцати субъектов.
    thin = set(region_codes[:PARTIAL_REGIONS]) if spec.coverage == "thin_last" else None

    # Ячейки с заглушками пропусков выбираются детерминированно.
    hidden = _pick_cells(spec.hidden_cells, region_codes, years, rng)
    missing = _pick_cells(spec.missing_cells, region_codes, years, rng)

    rows: list[dict[str, object]] = []

    def emit(territory: Territory, year: int, value: float | None) -> None:
        """Добавить строку набора, подставив заглушку вместо отсутствующего значения."""
        if value is None:
            source_value = SENTINEL_NO_DATA
        elif (territory.code, year) in hidden:
            source_value = SENTINEL_HIDDEN
        elif (territory.code, year) in missing:
            source_value = SENTINEL_NO_DATA
        else:
            source_value = value

        rows.append(
            {
                "section": spec.section,
                "indicator_code": spec.code,
                "indicator_name": spec.indicator,
                "subsection": subsection,
                "object_name": territory.source_name,
                "object_level": SOURCE_LEVELS[territory.level],
                "object_oktmo": "00000000",
                "object_okato": territory.okato,
                "year": year,
                "indicator_value": float(source_value),
                "indicator_unit": spec.unit,
                "comment": spec.comment,
                "source": EDITION_CURRENT,
                "version_date": VERSION_CODE,
            }
        )
        # Ранний выпуск публикует то же наблюдение с иным значением: именно так
        # в источнике возникают пересмотры официальной статистики.
        if spec.revised and year <= REVISION_LAST_YEAR and value is not None:
            rows.append(
                rows[-1]
                | {
                    "indicator_value": round(float(value) * 0.97, 3),
                    "source": EDITION_PREVIOUS,
                }
            )

    # Последний год набора заполнен частично: суммарные показатели за него ещё
    # не опубликованы. Это повторяет состояние реального источника.
    def skip_preliminary(year: int) -> bool:
        """Признак того, что за этот год показатель ещё не публикуется."""
        return year == PRELIMINARY_YEAR and spec.aggregation == "sum"

    for territory in regions:
        if territory.code not in allowed_regions:
            continue
        for year in years:
            if year not in allowed_years or skip_preliminary(year):
                continue
            if thin is not None and year == years[-1] and territory.code not in thin:
                continue
            # Крым и Севастополь появляются в статистике только с 2014 года.
            if territory.since is not None and year < territory.since:
                emit(territory, year, None)
                continue
            emit(territory, year, values[(territory.code, year)])

    if spec.coverage == "partial":
        # Ряд с недостаточным покрытием публикуется только по субъектам.
        return rows

    for territory in aggregates:
        for year in years:
            if skip_preliminary(year):
                continue
            emit(territory, year, _aggregate(spec, list(territory.components), year, values))

    for territory in districts:
        members = [item.code for item in regions if item.district == territory.code]
        for year in years:
            if skip_preliminary(year):
                continue
            emit(territory, year, _aggregate(spec, members, year, values))

    for year in years:
        if skip_preliminary(year):
            continue
        value = _aggregate(spec, region_codes, year, values)
        # Для единицы с укрупнением значение по стране публикуется в миллиардах рублей.
        if value is not None and "млрд" in spec.unit:
            value = round(value / 1000, 3)
        emit(country, year, value)

    return rows


def _pick_cells(
    count: int,
    region_codes: list[str],
    years: list[int],
    rng: np.random.Generator,
) -> set[tuple[str, int]]:
    """Выбрать заданное число ячеек «субъект — год» для подстановки заглушки."""
    if count <= 0:
        return set()
    picked: set[tuple[str, int]] = set()
    while len(picked) < count:
        code = region_codes[int(rng.integers(0, len(region_codes)))]
        year = years[int(rng.integers(0, len(years)))]
        picked.add((code, year))
    return picked
