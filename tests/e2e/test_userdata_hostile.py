"""
Названия из своей таблицы остаются текстом в браузере: показатель с ``<img onerror>``,
единица с разметкой, разрез с «=» в начале, название и источник таблицы, доска, её тексты
и карточки — на видах холста, в инструментах анализа, в подсказках графиков, в списке
выбора ряда, в картинках SVG и у читателя закрытой ссылки.
"""

from __future__ import annotations

import csv
import io
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Browser, Page

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# Срабатывает, только если строка стала разметкой.
TRAP = "<img src=x onerror=\"window.__hostile='{}'\">"
INDICATOR = f"{TRAP.format('indicator')} Случаи \"проверки\" & 'кавычки'"
RELATIVE = '=HYPERLINK("http://example.com/";"x") доля'
UNIT = "<b>единиц</b>"
GROUPS = (f"Все {TRAP.format('slice')}", "=1+1")
TITLE = f"{TRAP.format('title')} Таблица «проверки»"
NOTE = f"<svg onload=\"window.__hostile='note'\"></svg> {TRAP.format('note')}"

# Следы разметки: исполнение, элементы с обработчиками, теги из названий.
CHECK_SCRIPT = """() => {
    const handlers = [...document.querySelectorAll('*')].filter(
        (el) => [...el.attributes].some((attribute) => attribute.name.startsWith('on')));
    const tags = [...document.querySelectorAll('b, img[src="x"]')].filter(
        (el) => el.tagName === 'IMG' || el.textContent === 'единиц');
    return [
        ...(window.__hostile ? [`исполнено: ${window.__hostile}`] : []),
        ...handlers.map((el) => `обработчик: ${el.outerHTML.slice(0, 80)}`),
        ...tags.map((el) => `разметка: ${el.outerHTML.slice(0, 80)}`),
    ];
}"""

# Подсказки всех графиков страницы: по три точки каждого ряда, проверка после каждой;
# вторым — сколько подсказок показали название показателя текстом.
TOOLTIP_SCRIPT = f"""async () => {{
    const check = {CHECK_SCRIPT};
    const found = [];
    let literal = 0;
    const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
    for (const element of document.querySelectorAll('rl-chart')) {{
        element.scrollIntoView({{ block: 'center' }});
        for (let wait = 0; wait < 60 && !element.instance; wait += 1) await frame();
        const chart = element.instance;
        if (!chart) continue;
        const series = chart.getOption().series || [];
        for (let index = 0; index < series.length; index += 1) {{
            for (let point = 0; point < 3; point += 1) {{
                chart.dispatchAction({{ type: 'showTip', seriesIndex: index, dataIndex: point }});
                await frame();
                found.push(...check());
                const tips = [...element.querySelectorAll('div')].filter(
                    (div) => div.textContent.includes('<img src=x'));
                literal += tips.length ? 1 : 0;
            }}
        }}
        chart.dispatchAction({{ type: 'hideTip' }});
    }}
    return [found, literal];
}}"""


@pytest.fixture
def own_data(settings: Any, tmp_path: Path, warehouse_committed: Path, site: Any) -> Iterator[Any]:
    """Каталог таблиц на время сценария; сервер — с собранным складом."""
    settings.USERDATA_DIR = tmp_path / "userdata"
    cache.clear()
    yield site
    shutil.rmtree(settings.USERDATA_DIR, ignore_errors=True)


def _hostile_table() -> bytes:
    """Длинная таблица по всем субъектам: сумма с разметкой и доля с формулой в названии."""
    from apps.catalog.models import Territory

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["region", "year", "indicator", "unit", "group", "value"])
    names = Territory.objects.comparable().order_by("code").values_list("name_ru", flat=True)
    for number, name in enumerate(names):
        for year in range(2015, 2024):
            for slot, group in enumerate(GROUPS):
                count = 1000 + number * 37 + (year - 2015) * 11 + slot * 5
                writer.writerow([name, year, INDICATOR, UNIT, group, count])
                share = 10 + (number * 7 + year + slot) % 50
                writer.writerow([name, year, RELATIVE, "%", group, f"{share},5"])
    return buffer.getvalue().encode("utf-8")


def _dataset(client: Client) -> tuple[Any, list[str]]:
    """Собрать таблицу; вернуть её и ключи рядов."""
    from tests.support.userdata import build, describe, key_of, upload, version_of

    dataset = upload(client, "hostile.csv", _hostile_table())
    describe(client, dataset)
    response = build(
        client,
        dataset,
        title=TITLE,
        extra={"source_title": TITLE, "description": NOTE},
    )
    assert response.status_code == 302, response.content[:500]
    dataset.refresh_from_db()
    records = version_of(dataset).series.order_by("order")
    keys = [key_of(dataset, record) for record in records if record.values_count]
    assert len(keys) >= 2
    return dataset, keys


def _board(client: Client, keys: list[str], site_key: str) -> Any:
    """Доска с видами холста, инструментом, текстом и карточкой с названием и пояснением."""
    from apps.userdata.models import Board

    client.post(reverse("userdata:board-new"), {"title": TITLE})
    board = Board.objects.get()
    for target, query in (
        ("map", {"series": keys[0]}),
        ("compare", {"series": [keys[0], site_key]}),
        ("rankings", {"series": keys[1]}),
        ("distribution", {"series": keys[0]}),
        ("table", {"series": keys[1]}),
        ("inequality", {"series": keys[0]}),
    ):
        client.post(
            reverse("userdata:board-add"),
            {
                "target": target,
                "query_string": urlencode(query, doseq=True),
                "board": str(board.public_id),
            },
        )
    edit = reverse("userdata:board-edit", args=[board.public_id])
    client.post(edit, {"action": "add-text", "text": NOTE})
    board.refresh_from_db()
    card = next(block for block in board.blocks if block["kind"] == "view")
    client.post(edit, {"action": "change", "block": card["id"], "title": TITLE, "note": NOTE})
    return board


def _visit(page: Page, url: str, shown: list[int]) -> list[str]:
    """
    Открыть страницу, показать подсказки, раскрыть выбор ряда; вернуть найденные следы,
    в ``shown`` — сколько подсказок показали название текстом.
    """
    page.goto(url)
    page.wait_for_load_state("networkidle")
    found = page.evaluate(CHECK_SCRIPT)
    tips, literal = page.evaluate(TOOLTIP_SCRIPT)
    found += tips
    shown.append(literal)
    region = page.locator("[data-geo-map] [data-region]").first
    if region.count():
        region.hover()
    trigger = page.locator("rl-combobox .combobox__trigger").first
    if trigger.count() and trigger.is_visible():
        trigger.click()
        page.locator(".combobox__option").first.wait_for()
        page.keyboard.press("Escape")
    found += page.evaluate(CHECK_SCRIPT)
    return [f"{url}: {item}" for item in found]


def _svg_problems(path: Path) -> list[str]:
    """Картинка SVG разбирается как XML, и в ней нет элементов и обработчиков из названий."""
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    problems = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in {"img", "script", "foreignObject"}:
            problems.append(f"{path.name}: элемент {tag}")
        problems += [
            f"{path.name}: обработчик {name}" for name in element.attrib if name.startswith("on")
        ]
    return problems


def _save_svg(page: Page, host: str) -> list[str]:
    """Сохранить картинку SVG первого графика или карты в ``host``."""
    frame = page.locator(host).first
    frame.scroll_into_view_if_needed()
    frame.hover()
    button = page.locator(".chart-save", has_text="SVG").first
    with page.expect_download() as download:
        button.click()
    return _svg_problems(Path(download.value.path()))


def test_hostile_names_stay_text(
    page: Page, own_data: Any, registered_user: Any, browser: Browser
) -> None:
    from apps.catalog.models import Series

    client = Client()
    client.force_login(registered_user)
    dataset, keys = _dataset(client)
    site_key = Series.objects.exclude(key__startswith="u:").order_by("key").first().key
    board = _board(client, keys, site_key)

    page.context.add_cookies(
        [
            {
                "name": "regionlens_sessionid",
                "value": client.cookies["regionlens_sessionid"].value,
                "url": own_data.url,
            }
        ]
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))

    base = own_data.url
    one = urlencode({"series": keys[0]})
    pair = urlencode({"series": [keys[0], site_key], "x": keys[0], "y": site_key}, doseq=True)
    three = urlencode({"series": [*keys[:2], site_key]}, doseq=True)
    urls = [
        f"{base}{reverse('userdata:index')}",
        f"{base}{reverse('userdata:dataset', args=[dataset.public_id])}",
        f"{base}{reverse('userdata:related', args=[dataset.public_id])}?{one}",
        f"{base}{reverse('userdata:formula-new', args=[dataset.public_id])}",
        *(
            f"{base}{reverse(name)}?{urlencode({'series': key})}"
            for name in (
                "maps:choropleth",
                "rankings:index",
                "surface:distribution",
                "surface:table",
            )
            for key in keys[:2]
        ),
        f"{base}{reverse('compare:index')}?{three}",
        f"{base}{reverse('analytics:inequality')}?{one}",
        f"{base}{reverse('analytics:convergence')}?{one}",
        f"{base}{reverse('analytics:spatial')}?{one}",
        f"{base}{reverse('analytics:correlation')}?{pair}",
        f"{base}{reverse('analytics:index-builder')}?{three}",
        f"{base}{reverse('userdata:board', args=[board.public_id])}",
    ]
    problems: list[str] = []
    shown: list[int] = []
    for url in urls:
        problems += _visit(page, url, shown)

    # Картинки: карта, график динамики и карточка доски.
    page.goto(f"{base}{reverse('maps:choropleth')}?{one}")
    page.wait_for_load_state("networkidle")
    problems += _save_svg(page, "rl-choropleth")
    page.goto(f"{base}{reverse('compare:index')}?{one}")
    page.wait_for_load_state("networkidle")
    problems += _save_svg(page, "rl-chart")

    # Читатель закрытой ссылки на доску — без входа.
    page.goto(f"{base}{reverse('userdata:board', args=[board.public_id])}")
    page.wait_for_load_state("networkidle")
    page.locator("#own-shares form.own-share-form button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    link = page.locator("#own-share-url").input_value()
    reader = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        other = reader.new_page()
        other.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        problems += _visit(other, link, shown)
        other.locator(".board-card__open").first.click()
        other.wait_for_load_state("networkidle")
        problems += [f"{other.url}: {item}" for item in other.evaluate(CHECK_SCRIPT)]
    finally:
        reader.close()

    # Названия видны как текст.
    page.goto(f"{base}{reverse('userdata:dataset', args=[dataset.public_id])}")
    assert "<img src=x" in page.locator("h1").inner_text()
    problems += [f"окно: {message}" for message in dialogs]
    assert not problems, "\n".join(problems)
    # Подсказки действительно показывались — с названием текстом.
    assert sum(shown) > 0, shown
