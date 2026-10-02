"""
Сервис получения данных Банка России (``cbr.ru/dataservice``): ипотека, кредиты
физическим лицам и субъектам МСП, ставка по ипотеке — помесячно по субъектам с 2019 года.

Выпуск — снимок нужных показателей на дату их обновления в сервисе: ответы программного
интерфейса как есть, собранные в ZIP. Каждый снимок несёт всю историю, поэтому
смена значения между снимками — пересмотр.
"""

from __future__ import annotations

import io
import json
import re
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from . import network
from .base import PARSED_COLUMNS, Candidate, ParsedRelease
from .sheets import MONTHS, SheetFormatError
from .territories import region_codes, territory_code

code = "cbr"
title_ru = "Сервис получения данных Банка России"
title_en = "Bank of Russia data service"
publisher_ru = "Банк России"
publisher_en = "Bank of Russia"
# Короткое имя — в подписях у значений.
short_ru = "Банк России"
short_en = "Bank of Russia"
licence_ru = "Открытые данные Банка России, свободное использование со ссылкой на источник"
licence_en = "Bank of Russia open data, free use with attribution"
page_url = "https://www.cbr.ru/statistics/data-service/"
check_every = timedelta(days=1)
parser_version = 2

API_URL = "https://www.cbr.ru/dataservice"
# Первый год данных сервиса по субъектам.
FIRST_YEAR = 2019
# Время ZIP-записей постоянно: одинаковые ответы дают одинаковый файл и хэш.
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_PERIOD = re.compile(r"^\s*(\w+)\s+(\d{4})\s*$")
# «Архангельская область, в том числе Ненецкий автономный округ» у Банка России —
# сам округ: его значение вместе со строкой «…без данных по Ненецкому…» даёт итог области.
_OKRUG_OF_REGION = re.compile(r"^.*,\s*в том числе\s+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MeasureSpec:
    """Показатель сервиса и имя, под которым он лежит в разобранном выпуске."""

    measure: str
    publication: int
    indicator: int
    element: int  # второй разрез: «Всего» или «В рублях»
    unit: str  # единица по справочнику сервиса: смена — ошибка формата


MEASURES: tuple[MeasureSpec, ...] = (
    MeasureSpec("mortgage_count", 21, 44, 35, "единиц"),
    MeasureSpec("mortgage_volume", 21, 45, 35, "млн руб."),
    MeasureSpec("mortgage_debt", 21, 46, 35, "млн руб."),
    MeasureSpec("mortgage_overdue", 21, 47, 35, "млн руб."),
    # Срок и ставка публикуются только по кредитам в рублях.
    MeasureSpec("mortgage_term", 21, 48, 36, "месяцев"),
    MeasureSpec("mortgage_rate", 15, 34, 36, "% годовых"),
    MeasureSpec("retail_volume", 20, 41, 35, "млн руб."),
    MeasureSpec("retail_debt", 20, 42, 35, "млн руб."),
    MeasureSpec("retail_overdue", 20, 43, 35, "млн руб."),
    MeasureSpec("sme_volume", 23, 52, 35, "млн руб."),
    MeasureSpec("sme_debt", 23, 53, 35, "млн руб."),
    MeasureSpec("sme_overdue", 23, 54, 35, "млн руб."),
)


def publications() -> list[int]:
    """Публикации сервиса, из которых берутся показатели, в постоянном порядке."""
    return sorted({spec.publication for spec in MEASURES})


def queries() -> list[tuple[int, int, tuple[int, ...]]]:
    """Запросы данных: публикация, второй разрез и показатели — по одному на сочетание."""
    grouped: dict[tuple[int, int], list[int]] = {}
    for spec in MEASURES:
        grouped.setdefault((spec.publication, spec.element), []).append(spec.indicator)
    return [(pub, element, tuple(sorted(ids))) for (pub, element), ids in sorted(grouped.items())]


def discover() -> list[Candidate]:
    """Снимок на дату последнего обновления нужных показателей в сервисе."""
    updated: list[date] = []
    try:
        for publication in publications():
            wanted = {spec.indicator for spec in MEASURES if spec.publication == publication}
            listing = _json(f"{API_URL}/datasets?publicationId={publication}")
            for item in listing:
                if item.get("id") in wanted and item.get("updated_time"):
                    updated.append(datetime.fromisoformat(item["updated_time"]).date())
    except network.FetchError:
        return []
    if not updated:
        return []
    return [_candidate(max(updated))]


def identify(file_name: str, url: str = "") -> Candidate:
    """Снимок по имени файла ``cbr-dataservice-ГГГГ-ММ-ДД.zip``."""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", file_name)
    if match is None:
        raise ValueError(f"имя «{file_name}» не похоже на снимок сервиса (cbr-dataservice-ДАТА)")
    candidate = _candidate(date.fromisoformat(match.group(1)))
    return Candidate(
        url=url or candidate.url,
        release_code=candidate.release_code,
        title=candidate.title,
        reference_year=candidate.reference_year,
        published_on=candidate.published_on,
        file_name=file_name,
    )


def fetch(candidate: Candidate) -> bytes:
    """Ответы сервиса по всем показателям, собранные в ZIP: справочники и данные."""
    last_year = candidate.reference_year
    files: dict[str, bytes] = {}
    for publication in publications():
        files[f"datasetsEx_{publication}.json"] = _get(
            f"{API_URL}/datasetsEx?publicationId={publication}"
        )
    for publication, element, indicators in queries():
        parameters = [
            ("publicationId", publication),
            ("y1", FIRST_YEAR),
            ("y2", last_year),
            *(("i_ids", indicator) for indicator in indicators),
            ("m2_ids", element),
        ]
        name = f"dataEx_{publication}_{element}.json"
        files[name] = _get(f"{API_URL}/dataEx?{urllib.parse.urlencode(parameters)}")
    return bundle(files)


def bundle(files: dict[str, bytes]) -> bytes:
    """ZIP с постоянным временем записей: одинаковое содержимое — одинаковый хэш."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return buffer.getvalue()


def parse(files: Path) -> ParsedRelease:
    """Разобрать ответы сервиса; у каждого показателя должна быть строка каждого субъекта."""
    catalogs = {
        publication: _load(files, f"datasetsEx_{publication}.json")
        for publication in publications()
    }
    data = {
        (publication, element): _load(files, f"dataEx_{publication}_{element}.json")
        for publication, element, _indicators in queries()
    }

    frames: list[pd.DataFrame] = []
    report: dict[str, Any] = {"tables": {}}
    for spec in MEASURES:
        catalog = catalogs[spec.publication]
        names = {item["id"]: item["name"] for item in catalog.get("measures_1", [])}
        units = {item["id"]: item.get("name") or item.get("val") for item in catalog["units"]}
        rows = [
            row
            for row in data[(spec.publication, spec.element)].get("RawData", [])
            if row.get("indicator_id") == spec.indicator and row.get("measure_2_id") == spec.element
        ]
        if not rows:
            raise SheetFormatError(f"{spec.measure}: в ответе сервиса нет данных")
        found_units = {units.get(row.get("unit_id")) for row in rows}
        if found_units != {spec.unit}:
            listed = ", ".join(sorted(str(unit) for unit in found_units))
            raise SheetFormatError(f"{spec.measure}: единица «{listed}» вместо «{spec.unit}»")

        records = []
        unmatched: set[str] = set()
        for row in rows:
            label = names.get(row.get("measure_1_id"), "")
            territory = territory_code(_OKRUG_OF_REGION.sub("", label))
            if territory is None:
                unmatched.add(label)
                continue
            year, month = period_of(str(row.get("period", "")))
            value = row.get("value")
            records.append(
                {
                    "measure": spec.measure,
                    "territory_code": territory,
                    "year": year,
                    "period_kind": "month",
                    "period": month,
                    "value": None if value is None else float(value),
                    "hidden": False,
                    "preliminary": False,
                }
            )
        frame = pd.DataFrame(records, columns=list(PARSED_COLUMNS))
        missing = sorted(region_codes() - set(frame["territory_code"]))
        if missing:
            raise SheetFormatError(
                f"{spec.measure}: нет субъектов {', '.join(missing)}; "
                f"не распознаны: {'; '.join(sorted(unmatched)) or 'нет'}"
            )
        duplicated = frame.duplicated(["territory_code", "year", "period"])
        if duplicated.any():
            first = frame[duplicated].iloc[0]
            period = f"{first['period']:02d}.{first['year']}"
            raise SheetFormatError(
                f"{spec.measure}: {first['territory_code']} за {period} повторяется"
            )
        frames.append(frame)
        last = frame.sort_values(["year", "period"]).iloc[-1]
        # Вид отчёта — как у таблиц бюллетеня: его читает карточка выпуска в панели.
        report["tables"][spec.measure] = {
            "table": f"{spec.publication}/{spec.indicator}",
            "sheet": f"m2={spec.element}",
            "title": _dataset_title(catalog, spec.indicator),
            "values": int(frame["value"].notna().sum()),
            "hidden": 0,
            "regions": len(set(frame["territory_code"]) & region_codes()),
            "years": [int(frame["year"].min()), int(last["year"])],
            "last": f"{int(last['year'])}-{int(last['period']):02d}",
            "preliminary_years": [],
            "unmatched": sorted(unmatched),
        }
    return ParsedRelease(frame=pd.concat(frames, ignore_index=True), report=report)


def _dataset_title(catalog: dict[str, Any], indicator: int) -> str:
    """Название показателя в справочнике публикации."""
    return next(
        (
            str(item.get("name", ""))
            for item in catalog.get("indicators", [])
            if item["id"] == indicator
        ),
        "",
    )


def period_of(label: str) -> tuple[int, int]:
    """Год и месяц по подписи периода сервиса: «Июль 2026»."""
    match = _PERIOD.match(label)
    month = MONTHS.get(match.group(1).lower()) if match else None
    if match is None or month is None:
        raise SheetFormatError(f"период «{label}» не похож на «Месяц ГГГГ»")
    return int(match.group(2)), month


def _candidate(updated: date) -> Candidate:
    return Candidate(
        url=f"{API_URL}/dataEx",
        release_code=updated.isoformat(),
        title=f"по состоянию на {updated:%d.%m.%Y}",
        reference_year=updated.year,
        published_on=updated,
        file_name=f"cbr-dataservice-{updated.isoformat()}.zip",
    )


def _get(url: str) -> bytes:
    content, _remote = network.download(url)
    return content


def _json(url: str) -> Any:
    try:
        return json.loads(_get(url).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise network.FetchError(f"{url}: ответ не JSON ({error})") from error


def _load(files: Path, name: str) -> Any:
    path = next(iter(sorted(files.rglob(name))), None)
    if path is None:
        raise SheetFormatError(f"в снимке нет файла {name}")
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SheetFormatError(f"{name}: не JSON ({error})") from error
