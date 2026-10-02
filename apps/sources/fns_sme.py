"""
Статистика Единого реестра субъектов малого и среднего предпринимательства ФНС России
(``rmsp.nalog.ru``): число субъектов МСП и их работников по субъектам на 10-е число месяца.

Выпуск — сведения реестра на одну дату: ответ страницы статистики как есть. История
собирается при первом обращении — все даты, которые предлагает страница.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from . import network
from .base import PARSED_COLUMNS, Candidate, ParsedRelease
from .sheets import SheetFormatError
from .territories import normalize, region_codes, territory_code

code = "fns_sme"
title_ru = "Единый реестр субъектов малого и среднего предпринимательства, статистика"
title_en = "Unified register of small and medium-sized businesses, statistics"
publisher_ru = "ФНС России"
publisher_en = "Federal Tax Service of Russia"
# Короткое имя — в подписях у значений.
short_ru = "реестр МСП ФНС"
short_en = "Federal Tax Service SME register"
licence_ru = "Общедоступные сведения реестра, открытый доступ"
licence_en = "Public register data, open access"
page_url = "https://rmsp.nalog.ru/statistics.html"
check_every = timedelta(days=1)
parser_version = 3
# Первый сбор берёт все даты страницы, а не одну последнюю: выпуск несёт одну дату.
collect_history = True
max_per_check = None

DATA_URL = "https://rmsp.nalog.ru/statistics-proc.json"
_DATE = re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)")
_FILE_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DATE_LIST = re.compile(r'<select[^>]*id="statDate".*?</select>', re.DOTALL)
# Реестр ведётся по кодам регионов ФНС: 29 — Архангельская область без Ненецкого округа
# (83), 72 — Тюменская область без Ханты-Мансийского (86) и Ямало-Ненецкого (89) округов.
ALIASES = {
    "архангельская область": "RU-ARK",
    "тюменская область": "RU-TYU",
}
# Территории вне справочника проекта хранятся в разобранном выпуске с этой приставкой.
OUTSIDE_PREFIX = "ext:"

# Показатель разобранного выпуска, поле ответа и первая дата, на которую оно публикуется:
# число работников реестр показывает с 10.12.2016.
FIELDS = {
    "sme_count": ("cnt_total", date(2016, 8, 1)),
    "sme_workers": ("cnt_worker_total", date(2016, 12, 10)),
}
# Субъекты, которых нет в статистике реестра до этой даты: Ненецкого округа — до 10.03.2017.
ABSENT_BEFORE = {"RU-NEN": date(2017, 3, 10)}


def discover() -> list[Candidate]:
    """Даты реестра из списка на странице статистики, новые первыми."""
    try:
        text = network.page(page_url)
    except network.FetchError:
        return []
    # Даты — в списке выбора даты сведений: 10-е числа месяцев и 01.08.2016 — день
    # создания реестра. Остальные даты страницы к выпускам не относятся.
    block = _DATE_LIST.search(text)
    if block is None:
        return []
    days = {
        date(int(year), int(month), int(day)) for day, month, year in _DATE.findall(block.group(0))
    }
    return [_candidate(day) for day in sorted(days, reverse=True)]


def identify(file_name: str, url: str = "") -> Candidate:
    """Выпуск по имени файла ``rmsp-statistics-ГГГГ-ММ-ДД.json``."""
    match = _FILE_DATE.search(file_name)
    if match is None:
        raise ValueError(f"имя «{file_name}» не похоже на выпуск реестра (rmsp-statistics-ДАТА)")
    candidate = _candidate(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
    return Candidate(
        url=url or candidate.url,
        release_code=candidate.release_code,
        title=candidate.title,
        reference_year=candidate.reference_year,
        published_on=candidate.published_on,
        file_name=file_name,
    )


def fetch(candidate: Candidate) -> bytes:
    """Ответ страницы статистики на дату выпуска."""
    day = date.fromisoformat(candidate.release_code)
    content, _remote = network.post(DATA_URL, {"statDate": f"{day:%d.%m.%Y}"})
    return content


def parse(files: Path) -> ParsedRelease:
    """Разобрать ответ: строки страны, округов и субъектов на дату выпуска."""
    path = next(iter(sorted(files.rglob("*.json"))), None)
    if path is None:
        raise SheetFormatError("в выпуске нет файла JSON")
    try:
        rows = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SheetFormatError(f"{path.name}: не JSON ({error})") from error
    if not isinstance(rows, list) or not rows:
        raise SheetFormatError(f"{path.name}: пустой ответ")

    day = _stat_date(rows)
    fields = {measure: name for measure, (name, since) in FIELDS.items() if day >= since}
    records: list[dict[str, Any]] = []
    outside: list[str] = []
    for row in rows:
        label = str(row.get("cnt_name", ""))
        territory = territory_code(label, ALIASES)
        if territory is None:
            territory = OUTSIDE_PREFIX + normalize(label)
            outside.append(label.strip())
        for measure, name in fields.items():
            # У субъектов вне справочника поле бывает не заполнено; у своих — ошибка формата.
            if name not in row and not territory.startswith(OUTSIDE_PREFIX):
                raise SheetFormatError(f"{path.name}: у «{label}» нет поля {name}")
            value = row.get(name)
            records.append(
                {
                    "measure": measure,
                    "territory_code": territory,
                    "year": day.year,
                    "period_kind": "month",
                    "period": day.month,
                    "value": None if value is None else float(value),
                    "hidden": False,
                    "preliminary": False,
                }
            )
    frame = pd.DataFrame(records, columns=list(PARSED_COLUMNS))
    absent = {code for code, first in ABSENT_BEFORE.items() if day < first}
    missing = sorted(region_codes() - absent - set(frame["territory_code"]))
    if missing:
        raise SheetFormatError(
            f"{path.name}: нет субъектов {', '.join(missing)}; вне справочника: "
            f"{'; '.join(outside) or 'нет'}"
        )
    if frame.duplicated(["measure", "territory_code"]).any():
        raise SheetFormatError(f"{path.name}: территория повторяется")
    # Вид отчёта — как у таблиц бюллетеня: его читает карточка выпуска в панели.
    regions = len(set(frame["territory_code"]) & region_codes())
    report = {
        "date": day.isoformat(),
        "outside": outside,
        "tables": {
            measure: {
                "table": name,
                "sheet": f"{day:%d.%m.%Y}",
                "title": title_ru,
                "values": int(frame.loc[frame["measure"] == measure, "value"].notna().sum()),
                "hidden": 0,
                "regions": regions,
                "years": [day.year, day.year],
                "preliminary_years": [],
                "unmatched": outside,
            }
            for measure, name in fields.items()
        },
    }
    return ParsedRelease(frame=frame, report=report)


def _stat_date(rows: list[dict[str, Any]]) -> date:
    """Дата сведений реестра из ответа: одна на все строки."""
    found = {str(row.get("stat_date", ""))[:10] for row in rows}
    if len(found) != 1:
        raise SheetFormatError(f"в ответе несколько дат: {', '.join(sorted(found))}")
    match = _DATE.search(found.pop())
    if match is None:
        raise SheetFormatError("в ответе нет даты сведений")
    return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))


def _candidate(day: date) -> Candidate:
    return Candidate(
        url=f"{DATA_URL}?statDate={day:%d.%m.%Y}",
        release_code=day.isoformat(),
        title=f"на {day:%d.%m.%Y}",
        reference_year=day.year,
        published_on=day,
        file_name=f"rmsp-statistics-{day.isoformat()}.json",
    )
