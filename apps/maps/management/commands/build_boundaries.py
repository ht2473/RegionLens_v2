"""
Подготовка файла границ субъектов из Natural Earth 1:10 млн (общественное достояние).

Отбор 85 субъектов, приведение кодов, сдвиг долгот через 180-й меридиан, упрощение
контуров. Результат — ``static/geo/russia-regions.geojson`` в репозитории.
"""

from __future__ import annotations

import json
import math
import urllib.request
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# Административные единицы первого уровня, 1:10 млн (около 40 МБ). Набор 1:50 млн
# не годится: в нём Карелия присоединена к Мурманской области.
SOURCE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_10m_admin_1_states_provinces.geojson"
)


def _dataset_name(url: str) -> str:
    """Определить название исходного набора по адресу файла."""
    return url.rsplit("/", 1)[-1].removesuffix(".geojson")


# Атрибут, по которому в исходном наборе опознаётся страна.
COUNTRY_KEY = "adm0_a3"
COUNTRY_VALUE = "RUS"

# Приведение кодов к справочнику: у Крыма и Севастополя в источнике коды Украины,
# а коды Москвы и Московской области переставлены местами.
CODE_REMAP = {
    "UA-43": "RU-CR",
    "UA-40": "RU-SEV",
    "RU-MOS": "RU-MOW",
    "RU-MOW": "RU-MOS",
}

# Безымянный участок со служебным кодом — не субъект.
SKIP_CODES = frozenset({"RU-X01~"})

# Значения по умолчанию для упрощения геометрии.
DEFAULT_TOLERANCE = 0.03  # градусы; определяет степень прореживания контура
DEFAULT_PRECISION = 3  # знаков после запятой: около ста метров на местности
DEFAULT_MIN_AREA = 0.03  # градусы²; ниже этого порога остров не различим на карте

# Размер сетки черновой плиточной раскладки.
TILE_COLUMNS = 24
TILE_ROWS = 13

# Минимальное число точек в кольце: треугольник — вырожденный контур.
MIN_RING_POINTS = 4

# Ломаная из двух точек — отрезок, прореживать в ней нечего.
MIN_SIMPLIFIABLE_POINTS = 2


class Command(BaseCommand):
    """Подготовить файл границ субъектов для картограммы."""

    help = "Готовит static/geo/russia-regions.geojson из открытого набора Natural Earth"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument(
            "--input",
            type=Path,
            default=None,
            help="Путь к ранее скачанному файлу Natural Earth (без обращения в сеть)",
        )
        parser.add_argument(
            "--source",
            default=SOURCE_URL,
            help="Адрес исходного набора геометрии",
        )
        parser.add_argument(
            "--tolerance",
            type=float,
            default=DEFAULT_TOLERANCE,
            help=f"Допуск упрощения контуров в градусах (по умолчанию {DEFAULT_TOLERANCE})",
        )
        parser.add_argument(
            "--precision",
            type=int,
            default=DEFAULT_PRECISION,
            help=f"Знаков после запятой в координатах (по умолчанию {DEFAULT_PRECISION})",
        )
        parser.add_argument(
            "--min-area",
            type=float,
            default=DEFAULT_MIN_AREA,
            help="Минимальная площадь острова в квадратных градусах",
        )
        parser.add_argument(
            "--suggest-tiles",
            action="store_true",
            help="Напечатать черновую раскладку плиточной карты по координатам центров",
        )

    # -----------------------------------------------------------------------------------
    # Точка входа
    # -----------------------------------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Выполнить подготовку файла границ."""
        source = self._load_source(options["input"], options["source"])
        reference = self._load_reference()

        features = self._select_russia(source)
        self._check_composition(features, reference)

        output: list[dict[str, Any]] = []
        points_after = 0

        shifted = [_shift_longitudes(feature["geometry"]) for feature in features]
        points_before = sum(_count_points(geometry) for geometry in shifted)
        # Эксклав (Зеленоград у Москвы) мельче порога, но под ним дыра в соседе: без него
        # дыра осталась бы белой.
        holes = {
            frozenset(_ring_points(ring))
            for geometry in shifted
            for polygon in _polygons(geometry)
            for ring in polygon[1:]
        }
        prepared = [
            _drop_small_parts(geometry, options["min_area"], keep=holes) for geometry in shifted
        ]
        # Упрощение — всех субъектов сразу: общая граница соседей упрощается один раз.
        simplified = _simplify_shared(prepared, options["tolerance"])

        for feature, shape in zip(features, simplified, strict=True):
            code = self._code_of(feature)
            record = reference[code]
            geometry = _round_geometry(shape, options["precision"])
            points_after += _count_points(geometry)

            output.append(
                {
                    "type": "Feature",
                    # В name — код территории: названия не уникальны.
                    "properties": {
                        "name": code,
                        "code": code,
                        "name_ru": record["name_ru"],
                        "name_en": record["name_en"],
                        "abbr": record["abbr"],
                        "district": record["district"],
                    },
                    "geometry": geometry,
                }
            )

        output.sort(key=lambda item: item["properties"]["code"])
        path = self._write_geojson(output, _dataset_name(options["source"]))

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово: {path.name}, объектов — {len(output)}, "
                f"точек {points_before} → {points_after} "
                f"({path.stat().st_size / 1024:.0f} КБ)"
            )
        )

        label_points = {
            self._code_of(feature): (
                round(float(feature["properties"]["longitude"]), 4),
                round(float(feature["properties"]["latitude"]), 4),
            )
            for feature in features
        }
        self._write_geography(label_points, reference)

        if options["suggest_tiles"]:
            self._print_tile_draft(label_points, reference)

    # -----------------------------------------------------------------------------------
    # Чтение исходных данных
    # -----------------------------------------------------------------------------------

    def _load_source(self, path: Path | None, url: str) -> dict[str, Any]:
        """Прочитать исходный набор с диска или скачать его."""
        if path is not None:
            if not path.exists():
                raise CommandError(f"Файл не найден: {path}")
            self.stdout.write(f"Исходный набор: {path}")
            return json.loads(path.read_text(encoding="utf-8"))

        self.stdout.write(f"Загрузка исходного набора: {url}")
        try:
            with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310  # nosec B310 - адрес задан константой или ключом команды
                payload = response.read().decode("utf-8")
        except OSError as error:
            raise CommandError(
                f"Не удалось загрузить исходный набор: {error}. "
                "Скачайте файл вручную и укажите его через --input."
            ) from error
        return json.loads(payload)

    def _load_reference(self) -> dict[str, dict[str, Any]]:
        """Прочитать справочник территорий проекта."""
        path = settings.REFERENCE_DIR / "territories.json"
        if not path.exists():
            raise CommandError(f"Справочник территорий не найден: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return {item["code"]: item for item in data["regions"]}

    def _select_russia(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        """Отобрать из мирового набора территории Российской Федерации."""
        return [
            feature
            for feature in source.get("features", [])
            if feature.get("properties", {}).get(COUNTRY_KEY) == COUNTRY_VALUE
            and self._code_of(feature) not in SKIP_CODES
        ]

    def _code_of(self, feature: dict[str, Any]) -> str:
        """Определить код территории по атрибутам исходного объекта."""
        raw = feature["properties"].get("iso_3166_2") or ""
        return CODE_REMAP.get(raw, raw)

    def _check_composition(
        self, features: list[dict[str, Any]], reference: dict[str, dict[str, Any]]
    ) -> None:
        """Сверить состав геометрии со справочником: расхождение останавливает сборку."""
        codes = [self._code_of(feature) for feature in features]
        duplicates = {code for code in codes if codes.count(code) > 1}
        missing = sorted(set(reference) - set(codes))
        unknown = sorted(set(codes) - set(reference))

        problems: list[str] = []
        if duplicates:
            problems.append("коды повторяются: " + ", ".join(sorted(duplicates)))
        if missing:
            problems.append("нет геометрии для: " + ", ".join(missing))
        if unknown:
            problems.append("геометрия без справочника: " + ", ".join(unknown))

        if problems:
            raise CommandError(
                "Состав геометрии не совпадает со справочником; " + "; ".join(problems)
            )

        self.stdout.write(self.style.SUCCESS(f"  ✓ Состав совпадает со справочником: {len(codes)}"))

    # -----------------------------------------------------------------------------------
    # Запись результатов
    # -----------------------------------------------------------------------------------

    def _write_geojson(self, features: list[dict[str, Any]], dataset: str) -> Path:
        """Записать подготовленный файл границ."""
        path = Path(settings.BASE_DIR) / "static" / "geo" / "russia-regions.geojson"
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "type": "FeatureCollection",
            "meta": {
                "title": "Границы субъектов Российской Федерации",
                "source": f"Natural Earth, {dataset}",
                "licence": "public domain (CC0)",
                "composition": "85 субъектов в составе, соответствующем набору данных Росстата",
                "projection": "EPSG:4326; долготы приведены к непрерывному диапазону 19…191°",
            },
            "features": features,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
            newline="\n",
        )
        return path

    def _write_geography(
        self, label_points: dict[str, tuple[float, float]], reference: dict[str, dict[str, Any]]
    ) -> None:
        """Обновить координаты центров в географическом справочнике, не трогая раскладку плиток."""
        path = settings.REFERENCE_DIR / "geography.json"
        existing: dict[str, Any] = {}
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))

        regions: dict[str, Any] = existing.get("regions", {})
        for code in sorted(reference):
            longitude, latitude = label_points[code]
            record = regions.setdefault(code, {})
            record["longitude"] = longitude
            record["latitude"] = latitude

        payload = {
            "meta": {
                "title": "Географические характеристики субъектов для карт",
                "coordinates_source": "Natural Earth, точки подписей административных единиц",
                "tiles_note": (
                    "Позиции плиточной картограммы составлены вручную: сетка сохраняет "
                    "взаимное расположение субъектов, но не их площадь и форму."
                ),
                "tile_grid": {"columns": TILE_COLUMNS, "rows": TILE_ROWS},
            },
            "regions": regions,
        }
        # Перевод строки — явно: в Windows write_text пишет CRLF, и справочник менялся бы целиком.
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )
        self.stdout.write(f"Обновлён справочник координат: {path.name}")

    def _print_tile_draft(
        self, label_points: dict[str, tuple[float, float]], reference: dict[str, dict[str, Any]]
    ) -> None:
        """Напечатать черновую раскладку плиточной карты по центрам; её правят вручную."""
        longitudes = [point[0] for point in label_points.values()]
        latitudes = [point[1] for point in label_points.values()]
        lon_min, lon_max = min(longitudes), max(longitudes)
        lat_min, lat_max = min(latitudes), max(latitudes)

        occupied: dict[tuple[int, int], str] = {}
        for code in sorted(label_points, key=lambda item: label_points[item][0]):
            longitude, latitude = label_points[code]
            column = int((longitude - lon_min) / (lon_max - lon_min) * (TILE_COLUMNS - 1))
            row = int((lat_max - latitude) / (lat_max - lat_min) * (TILE_ROWS - 1))
            column, row = _nearest_free_cell(occupied, column, row)
            occupied[(column, row)] = code

        for cell, code in sorted(occupied.items(), key=lambda item: (item[0][1], item[0][0])):
            column, row = cell
            self.stdout.write(
                f'  "{code}": {{"tile_x": {column}, "tile_y": {row}}},'
                f"  # {reference[code]['name_ru']}"
            )


# ---------------------------------------------------------------------------------------
# Преобразования геометрии
# ---------------------------------------------------------------------------------------


def _polygons(geometry: dict[str, Any]) -> list[list[list[list[float]]]]:
    """Представить геометрию единообразно — списком многоугольников."""
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    return geometry["coordinates"]


def _rebuild(polygons: list[list[list[list[float]]]]) -> dict[str, Any]:
    """Собрать геометрию обратно, выбрав подходящий тип."""
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _count_points(geometry: dict[str, Any]) -> int:
    """Подсчитать число точек в геометрии."""
    return sum(len(ring) for polygon in _polygons(geometry) for ring in polygon)


def _shift_longitudes(geometry: dict[str, Any]) -> dict[str, Any]:
    """Сдвинуть отрицательные долготы на 360°: часть Чукотки лежит за 180-м меридианом."""
    polygons = [
        [
            [[point[0] + 360 if point[0] < 0 else point[0], point[1]] for point in ring]
            for ring in polygon
        ]
        for polygon in _polygons(geometry)
    ]
    return _rebuild(polygons)


def _ring_area(ring: list[list[float]]) -> float:
    """Вычислить площадь кольца в квадратных градусах по формуле шнурования."""
    total = 0.0
    for index in range(len(ring) - 1):
        x1, y1 = ring[index]
        x2, y2 = ring[index + 1]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def _drop_small_parts(
    geometry: dict[str, Any],
    min_area: float,
    keep: set[frozenset[tuple[float, float]]] | None = None,
) -> dict[str, Any]:
    """
    Отбросить мелкие острова, всегда сохраняя наибольшую часть территории. Часть, чей
    контур — дыра в соседнем субъекте (``keep``), сохраняется при любой площади.
    """
    polygons = _polygons(geometry)
    areas = [_ring_area(polygon[0]) for polygon in polygons]
    largest = areas.index(max(areas))

    kept = [
        polygon
        for index, polygon in enumerate(polygons)
        if index == largest
        or areas[index] >= min_area
        or (keep is not None and frozenset(_ring_points(polygon[0])) in keep)
    ]
    return _rebuild(kept)


def _perpendicular_distance(
    point: list[float], start: list[float], end: list[float], scale: float
) -> float:
    """
    Найти расстояние от точки до прямой, проходящей через концы отрезка.

    Долгота сжимается множителем ``scale`` = cos(широты), чтобы упрощение шло равномерно.
    """
    x0, y0 = point[0] * scale, point[1]
    x1, y1 = start[0] * scale, start[1]
    x2, y2 = end[0] * scale, end[1]

    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x0 - x1, y0 - y1)

    return abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / math.hypot(dx, dy)


def _douglas_peucker(
    points: list[list[float]], tolerance: float, scale: float
) -> list[list[float]]:
    """Прорядить ломаную алгоритмом Дугласа — Пекера; без рекурсии — из-за глубины стека."""
    if len(points) <= MIN_SIMPLIFIABLE_POINTS:
        return points

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue

        farthest = -1
        max_distance = 0.0
        for index in range(first + 1, last):
            distance = _perpendicular_distance(points[index], points[first], points[last], scale)
            if distance > max_distance:
                max_distance = distance
                farthest = index

        if max_distance > tolerance and farthest > 0:
            keep[farthest] = True
            stack.append((first, farthest))
            stack.append((farthest, last))

    return [point for point, flag in zip(points, keep, strict=True) if flag]


Point = tuple[float, float]


def _arc_scale(points: list[Point] | tuple[Point, ...]) -> float:
    """Множитель сжатия долготы для участка: cos средней широты, не меньше 0,1."""
    latitudes = [point[1] for point in points]
    return max(math.cos(math.radians(sum(latitudes) / len(latitudes))), 0.1)


def _simplify_arc(arc: tuple[Point, ...], tolerance: float) -> list[Point]:
    """
    Упростить участок границы между двумя узлами, сохранив концы. Участок, сжатый до
    отрезка, сохраняет самую далёкую точку: иначе кольцо из двух участков выродится.
    """
    points = [list(point) for point in arc]
    scale = _arc_scale(arc)
    kept = _douglas_peucker(points, tolerance, scale)
    if len(kept) == MIN_SIMPLIFIABLE_POINTS and len(points) > MIN_SIMPLIFIABLE_POINTS:
        farthest = max(
            range(1, len(points) - 1),
            key=lambda index: _perpendicular_distance(points[index], points[0], points[-1], scale),
        )
        kept = [points[0], points[farthest], points[-1]]
    return [(point[0], point[1]) for point in kept]


def _canonical(sequence: tuple[Point, ...]) -> tuple[tuple[Point, ...], bool]:
    """Одно написание участка для обоих соседей: прямое или обратное; второе — «перевёрнут ли»."""
    backward = sequence[::-1]
    return (backward, True) if backward < sequence else (sequence, False)


def _canonical_ring(points: list[Point]) -> tuple[tuple[Point, ...], bool]:
    """Одно написание замкнутого кольца без узлов: с наименьшей точки, в одну из сторон."""
    start = points.index(min(points))
    forward = tuple(points[start:] + points[:start])
    backward = (forward[0], *forward[1:][::-1])
    return (backward, True) if backward < forward else (forward, False)


def _split_at_junctions(points: list[Point], junctions: set[Point]) -> list[tuple[Point, ...]]:
    """Разрезать кольцо (без замыкающей точки) на участки от узла до узла."""
    cuts = [index for index, point in enumerate(points) if point in junctions]
    rotated = points[cuts[0] :] + points[: cuts[0]]
    offsets = [index - cuts[0] for index in cuts]
    arcs = []
    for number, start in enumerate(offsets):
        if number + 1 < len(offsets):
            arcs.append(tuple(rotated[start : offsets[number + 1] + 1]))
        else:
            # Последний участок замыкается на первом узле.
            arcs.append((*rotated[start:], rotated[0]))
    return arcs


def _simplify_shared(geometries: list[dict[str, Any]], tolerance: float) -> list[dict[str, Any]]:
    """
    Упростить контуры всех субъектов так, чтобы общая граница соседей упрощалась один раз.

    Кольца режутся на участки в узлах — там, где у точки разные соседи по контурам (сходятся
    три субъекта или расходятся два). Участок упрощается однажды и достаётся обоим соседям
    в их направлении обхода. Упрощённая у каждого по-своему граница расходится, и между
    соседями остаются щели фона — во врезках Москвы и Петербурга они видны глазом.
    Кольцо без узлов (остров, анклав целиком в соседе) упрощается целиком, тоже однажды.
    """
    layouts = [
        [[_ring_points(ring) for ring in polygon] for polygon in _polygons(geometry)]
        for geometry in geometries
    ]
    neighbours: dict[Point, set[frozenset[Point]]] = {}
    for layout in layouts:
        for polygon in layout:
            for points in polygon:
                for index, point in enumerate(points):
                    pair = frozenset((points[index - 1], points[(index + 1) % len(points)]))
                    neighbours.setdefault(point, set()).add(pair)
    junctions = {point for point, pairs in neighbours.items() if len(pairs) > 1}

    done: dict[tuple[Point, ...], list[Point]] = {}

    def simplified_ring(points: list[Point]) -> list[list[float]] | None:
        if not any(point in junctions for point in points):
            canonical, flipped = _canonical_ring(points)
            if canonical not in done:
                done[canonical] = _simplify_arc((*canonical, canonical[0]), tolerance)
            ring = done[canonical]
            ring = ring[::-1] if flipped else ring
        else:
            ring = []
            for arc in _split_at_junctions(points, junctions):
                canonical, flipped = _canonical(arc)
                if canonical not in done:
                    done[canonical] = _simplify_arc(canonical, tolerance)
                part = done[canonical][::-1] if flipped else done[canonical]
                ring.extend(part if not ring else part[1:])
        if len(ring) < MIN_RING_POINTS:
            return None
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        return [list(point) for point in ring]

    result = []
    for geometry, layout in zip(geometries, layouts, strict=True):
        polygons: list[list[list[list[float]]]] = []
        for polygon in layout:
            rings = [simplified_ring(points) for points in polygon]
            if rings and rings[0] is not None:
                polygons.append([ring for ring in rings if ring is not None])
        # Ни одно кольцо не пережило упрощения — остаётся исходная геометрия.
        result.append(_rebuild(polygons) if polygons else geometry)
    return result


def _ring_points(ring: list[list[float]]) -> list[Point]:
    """Точки кольца без замыкающей и без повторов подряд."""
    points: list[Point] = []
    for point in ring:
        current = (point[0], point[1])
        if not points or points[-1] != current:
            points.append(current)
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _round_geometry(geometry: dict[str, Any], precision: int) -> dict[str, Any]:
    """
    Округлить координаты: избыточная точность увеличивает файл, но не карту. Точки,
    совпавшие после округления подряд, сливаются.
    """
    polygons = []
    for polygon in _polygons(geometry):
        rings = []
        for ring in polygon:
            rounded: list[list[float]] = []
            for point in ring:
                current = [round(point[0], precision), round(point[1], precision)]
                if not rounded or rounded[-1] != current:
                    rounded.append(current)
            rings.append(rounded)
        polygons.append(rings)
    return _rebuild(polygons)


def _nearest_free_cell(
    occupied: dict[tuple[int, int], str], column: int, row: int
) -> tuple[int, int]:
    """Найти ближайшую свободную ячейку сетки для черновой раскладки плиток."""
    if (column, row) not in occupied:
        return column, row

    for radius in range(1, max(TILE_COLUMNS, TILE_ROWS)):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if abs(dx) != radius and abs(dy) != radius:
                    continue
                candidate = (column + dx, row + dy)
                if candidate in occupied:
                    continue
                if 0 <= candidate[0] < TILE_COLUMNS and 0 <= candidate[1] < TILE_ROWS:
                    return candidate
    return column, row
