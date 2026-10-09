"""
Проверки основного набора показателей: паспорт, главная, каталог, список выбора и документ.

Синтетический склад содержит первые двенадцать рядов набора.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.constants import BreakKind
from apps.catalog.models import Series, SeriesBreak, Territory
from apps.catalog.passport import build_passport
from apps.catalog.selectors import series_options, series_options_version
from apps.exports.constants import ReportKind
from apps.exports.reports import build_report
from apps.warehouse.queries import featured_set, featured_snapshot, territory_positions
from tests.support import synthetic

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

TATARSTAN = "RU-TA"


@pytest.fixture
def featured_keys(warehouse: Any) -> list[str]:
    """Ключи основного набора, которые есть в синтетическом складе."""
    keys = [item.key for item in featured_set().series]
    return list(Series.objects.filter(key__in=keys).values_list("key", flat=True))


class TestPositions:
    """Выборка положения территории по основному набору."""

    def test_one_row_per_known_series(self, featured_keys: list[str]) -> None:
        """По каждому ряду набора, который есть в складе, — одна строка с местом и страной."""
        rows = territory_positions(TATARSTAN, [item.key for item in featured_set().series])
        found = {row["series_key"] for row in rows}
        assert found <= set(featured_keys)
        assert len(found) == len(rows)
        assert rows
        for row in rows:
            assert row["territories"] >= 60
            assert row["country_value"] is not None
            assert 1 <= row["rank_desc"] <= row["territories"]

    def test_past_value_is_five_years_back(self, featured_keys: list[str]) -> None:
        """Значение для вывода об изменении взято ровно пятью годами раньше."""
        rows = territory_positions(TATARSTAN, featured_keys)
        with_past = [row for row in rows if row["past_year"] is not None]
        assert with_past
        assert all(row["year"] - row["past_year"] == 5 for row in with_past)


class TestPassport:
    """Паспорт региона понятным языком."""

    def test_summary_starts_with_population(self, featured_keys: list[str]) -> None:
        """Главное начинается с численности: она задаёт масштаб остальному."""
        passport = build_passport(TATARSTAN)
        assert passport.summary[0].startswith("Население — ")
        assert passport.total == len(featured_set().series)

    def test_assessment_uses_only_assessed_series(self, featured_keys: list[str]) -> None:
        """В сильные и слабые стороны не попадают абсолютные и нейтральные ряды."""
        passport = build_passport(TATARSTAN)
        for position in [*passport.strengths, *passport.weaknesses]:
            assert position.is_assessed
            assert not position.series.absolute

    def test_break_inside_window_blocks_change(self, featured_keys: list[str]) -> None:
        """Методический разрыв внутри пяти лет отменяет вывод об изменении."""
        passport = build_passport(TATARSTAN)
        target = next(p for p in passport.positions if p.past_year is not None)
        SeriesBreak.objects.create(
            series=Series.objects.get(key=target.series.key),
            year=target.year,
            kind=BreakKind.METHODOLOGY,
        )
        changed = build_passport(TATARSTAN).by_key()[target.series.key]
        assert changed.break_year == target.year
        assert "разрыв сопоставимости" in changed.trend

    def test_territory_break_keeps_region_change(self, featured_keys: list[str]) -> None:
        """Смена состава округов прерывает ряд страны, но не ряд субъекта."""
        passport = build_passport(TATARSTAN)
        target = next(
            p
            for p in passport.positions
            if p.past_year is not None and not p.series.is_growth_index and p.break_year is None
        )
        SeriesBreak.objects.create(
            series=Series.objects.get(key=target.series.key),
            year=target.year,
            kind=BreakKind.TERRITORY,
        )
        changed = build_passport(TATARSTAN).by_key()[target.series.key]
        assert changed.break_year is None
        assert changed.country_break
        assert "по России" not in changed.trend

    def test_foreign_territory_break_is_ignored(self, featured_keys: list[str]) -> None:
        """Разрыв, отнесённый к другому субъекту, этого региона не касается."""
        passport = build_passport(TATARSTAN)
        target = next(p for p in passport.positions if p.past_year is not None)
        SeriesBreak.objects.create(
            series=Series.objects.get(key=target.series.key),
            territory=Territory.objects.get(code="RU-MOW"),
            year=target.year,
            kind=BreakKind.METHODOLOGY,
        )
        changed = build_passport(TATARSTAN).by_key()[target.series.key]
        assert changed.break_year == target.break_year

    def test_page_shows_plain_language(self, client: Client, featured_keys: list[str]) -> None:
        """Страница паспорта несёт главное, выводы и таблицу по темам."""
        slug = Territory.objects.get(code=TATARSTAN).slug
        response = client.get(reverse("catalog:territory-detail", kwargs={"slug": slug}))
        content = response.content.decode()

        assert response.status_code == 200
        assert 'class="passport-lead"' in content
        assert "Все основные показатели" in content
        assert "место из" in content
        metrics = response.context["metrics"]
        assert metrics
        assert all(metric["series"].headline for metric in metrics)
        assert all(metric["position"] is not None for metric in metrics)

    def test_tiles_are_drawn_by_the_server(self, client: Client, featured_keys: list[str]) -> None:
        """
        Миниатюры плиток — путь SVG с сервера, полоска места — доля без запятой.

        Библиотека графиков ради восьми миниатюр не грузится; дробь, записанная
        шаблоном по-русски («0,62»), не разобралась бы свойством оформления.
        """
        slug = Territory.objects.get(code=TATARSTAN).slug
        response = client.get(reverse("catalog:territory-detail", kwargs={"slug": slug}))
        content = response.content.decode()
        assert 'class="sparkline"' in content
        assert "kpi__sparkline" not in content
        assert re.search(r'style="--at: [01]\.\d{3}"', content)

    def test_locator_marks_the_region(self, client: Client, featured_keys: list[str]) -> None:
        """Карта-указатель выделяет сам регион, соседние субъекты ведут в свои паспорта."""
        territory = Territory.objects.get(code=TATARSTAN)
        response = client.get(reverse("catalog:territory-detail", kwargs={"slug": territory.slug}))
        locator = response.context["locator"]
        assert locator is not None
        own = [region for region in locator["regions"] if region["code"] == TATARSTAN]
        assert own and own[0]["selected"]
        assert own[0]["fill"] == "var(--map-focus)"

    def test_every_side_is_listed(self, client: Client, featured_keys: list[str]) -> None:
        """Сильные и слабые стороны показаны все: сверх первых шести — под «ещё»."""
        slug = Territory.objects.get(code=TATARSTAN).slug
        response = client.get(reverse("catalog:territory-detail", kwargs={"slug": slug}))
        passport = response.context["passport"]
        columns = response.context["passport_columns"]
        assert columns[0]["count"] == len(passport.strengths)
        assert [*columns[0]["items"], *columns[0]["more"]] == passport.strengths
        assert [*columns[1]["items"], *columns[1]["more"]] == passport.weaknesses

    def test_extra_sides_follow_in_the_same_list(
        self, client: Client, featured_keys: list[str]
    ) -> None:
        """Пункты сверх шести — в том же перечне (data-more-item), кнопка «Ещё N» — после него."""
        slug = Territory.objects.get(code=TATARSTAN).slug
        response = client.get(reverse("catalog:territory-detail", kwargs={"slug": slug}))
        content = response.content.decode()
        columns = response.context["passport_columns"]
        extra = sum(len(column["more"]) for column in columns)
        assert content.count("data-more-item") == extra
        assert '<details class="sides__more"' not in content
        for column in columns:
            if column["more"]:
                toggle = re.search(
                    rf'<button[^>]*aria-controls="sides-{column["modifier"]}"[^>]*>', content
                )
                assert toggle and "hidden" in toggle.group(0)

    def test_themes_fold_with_a_verdict_summary(
        self, client: Client, featured_keys: list[str]
    ) -> None:
        """
        Темы таблицы сворачиваются на любой ширине (data-fold), в строке темы — сводка
        оценок; «Развернуть все» без сценариев скрыта. Строки тем приходят при раскрытии.
        """
        slug = Territory.objects.get(code=TATARSTAN).slug
        response = client.get(reverse("catalog:territory-detail", kwargs={"slug": slug}))
        content = response.content.decode()
        themes = response.context["passport"].themes
        assert themes
        assert len(re.findall(r"<tbody[^>]* data-fold[ >]", content)) == len(themes)
        assert content.count('data-fold-src="?rows=theme-') == len(themes)
        assert 'class="facts-table__title"' not in content
        assert "data-fold-narrow>" not in content
        assert re.search(r"<button[^>]*data-fold-all[^>]*hidden", content)
        for block in themes:
            assert block.better == sum(1 for p in block.positions if p.tone == "good")
            assert block.worse == sum(1 for p in block.positions if p.tone == "bad")
            assert block.better + block.worse <= len(block.positions)

    def test_theme_rows_come_on_demand(self, client: Client, featured_keys: list[str]) -> None:
        """
        Строки темы — по ?rows=theme-<тема>, все — по ?rows=all; ?themes=all отдаёт таблицу
        целиком в странице (без сценариев).
        """
        slug = Territory.objects.get(code=TATARSTAN).slug
        url = reverse("catalog:territory-detail", kwargs={"slug": slug})
        themes = client.get(url).context["passport"].themes
        first = themes[0]

        one = client.get(url, {"rows": f"theme-{first.theme.slug}"}).content.decode()
        assert one.count("<tbody data-rows=") == 1
        assert one.count('class="facts-table__title"') == len(first.positions)

        every = client.get(url, {"rows": "all"}).content.decode()
        assert every.count("<tbody data-rows=") == len(themes)

        full = client.get(url, {"themes": "all"}).content.decode()
        assert "data-fold-src" not in full
        total = sum(len(block.positions) for block in themes)
        assert full.count('class="facts-table__title"') == total

    def test_sections_are_listed_in_a_bar(self, client: Client, featured_keys: list[str]) -> None:
        """Полоса-оглавление ведёт к каждому разделу паспорта, и цели на странице есть."""
        slug = Territory.objects.get(code=TATARSTAN).slug
        content = client.get(
            reverse("catalog:territory-detail", kwargs={"slug": slug})
        ).content.decode()
        bar = re.search(r'<nav class="toc-pills passport__toc".*?</nav>', content, re.S)
        assert bar
        anchors = re.findall(r'href="#([\w-]+)"', bar.group(0))
        assert anchors[-3:] == ["passport-timeline", "passport-similar", "passport-facts"]
        for anchor in anchors:
            assert f'id="{anchor}"' in content


class TestSnapshot:
    """Плитки главных показателей."""

    def test_headline_only_by_default(self, featured_keys: list[str]) -> None:
        """Витрина показывает только главные показатели, таблица — весь набор."""
        headline = featured_snapshot("RU")
        everything = featured_snapshot("RU", headline_only=False)
        assert headline
        assert all(entry["series"].headline for entry in headline)
        assert {entry["series"].key for entry in everything} == set(featured_keys)

    def test_sparkline_ends_with_current_value(self, featured_keys: list[str]) -> None:
        """Миниатюрный график кончается последним известным значением."""
        for entry in featured_snapshot("RU"):
            assert entry["sparkline"][-1]["year"] == entry["current"]["year"]
            assert entry["sparkline"][-1]["value"] == entry["current"]["value"]


class TestCatalogThemes:
    """Каталог: основной набор по темам и полный перечень."""

    def test_bare_address_opens_themes(self, client: Client, featured_keys: list[str]) -> None:
        """Адрес без параметров открывает темы, а не перечень из 2 952 рядов."""
        response = client.get(reverse("catalog:indicator-list"))
        assert response.status_code == 200
        assert "catalog/indicator_themes.html" in [t.name for t in response.templates]
        themes = response.context["themes"]
        shown = {row["item"].key for block in themes for row in block["rows"]}
        assert shown == set(featured_keys)
        assert all(row["tile"] is not None for block in themes for row in block["rows"])
        # Вместо вопросов-чипов — поле поиска с примерами.
        assert response.context["search_examples"]
        assert 'class="find-form"' in response.content.decode()

    def test_tile_leads_to_the_indicator_page(
        self, client: Client, featured_keys: list[str]
    ) -> None:
        """Плитка — вход на страницу показателя: с ключом ряда, значением и ходом страны."""
        response = client.get(reverse("catalog:indicator-list"))
        content = response.content.decode()
        row = response.context["themes"][0]["rows"][0]
        page = reverse("catalog:series-detail", kwargs={"slug": row["series"].indicator.slug})
        assert f'href="{page}?series={row["item"].key}"' in content
        assert row["tile"]["value"] in content
        assert 'class="sparkline"' in content

    def test_full_list_is_a_separate_mode(self, client: Client, featured_keys: list[str]) -> None:
        """Полный перечень открывается режимом и не теряет его в форме отбора."""
        response = client.get(reverse("catalog:indicator-list"), {"view": "all"})
        content = response.content.decode()
        assert response.context["paginator"].count >= len(featured_keys)
        assert '<input type="hidden" name="view" value="all">' in content

    def test_themes_without_warehouse(self, client: Client, seeded: None) -> None:
        """Без склада темы открываются с объяснением, а не с ошибкой."""
        response = client.get(reverse("catalog:indicator-list"))
        assert response.status_code == 200
        assert response.context["warehouse_ready"] is False


@pytest.fixture
def seeded(db: None, reference_seed: None, settings: Any, tmp_path: Any) -> None:
    """Справочник без склада: файл склада указывает в пустоту."""
    from apps.warehouse import duckdb_client

    settings.DUCKDB_PATH = tmp_path / "absent.duckdb"
    duckdb_client.close_connections()


class TestSeriesOptions:
    """Список выбора ряда: основной набор по темам первым."""

    def test_featured_group_comes_first(self, featured_keys: list[str]) -> None:
        """Первая группа — тема основного набора с короткими названиями, без повторов ниже."""
        options = series_options()
        first = options[0]
        assert first["theme"]
        assert first["title"] in {theme.title for theme in featured_set().themes}
        titles = {item.key: item.short_title for item in featured_set().series}
        assert [item["title"] for item in first["items"]] == [
            titles[item["key"]] for item in first["items"]
        ]
        keys = [item["key"] for group in options for item in group["items"]]
        assert len(keys) == len(set(keys))
        assert all(item["search"] for item in first["items"])

    def test_markup_carries_search_words(self, client: Client, featured_keys: list[str]) -> None:
        """Пункт основного набора несёт слова сборника для поиска."""
        body = client.get(
            reverse("catalog:series-options"), {"v": series_options_version()}
        ).content.decode()
        assert body.startswith('<optgroup label="')
        assert body.split(">", 1)[0].endswith("data-theme")
        assert "data-search=" in body
        assert "data-hint=" in body

    def test_selected_featured_series_is_named_shortly(
        self, client: Client, featured_keys: list[str]
    ) -> None:
        """Выбранный ряд набора назван коротко и стоит в группе своей темы."""
        key = f"{synthetic.GRP_PER_CAPITA_CODE}:00"
        body = client.get(reverse("maps:choropleth"), {"series": key}).content.decode()
        select = re.search(r'<select[^>]*id="series-select".*?</select>', body, re.S)
        assert select is not None
        theme = featured_set().theme(featured_set().by_key()[key].theme)
        assert theme is not None
        assert f'label="{theme.title}"' in select.group(0)
        assert "ВРП на душу населения" in select.group(0)

    def test_version_follows_featured_file(self, featured_keys: list[str]) -> None:
        """Отпечаток перечня включает отпечаток файла набора."""
        assert featured_set().digest in series_options_version()


class TestPassportDocument:
    """Документ «паспорт региона» повторяет страницу."""

    def test_document_lists_featured_positions(self, featured_keys: list[str]) -> None:
        """В документе — основные показатели с выводами словами."""
        document = build_report(ReportKind.TERRITORY, {"territory": TATARSTAN}, max_rows=200)
        headings = [section.heading for section in document.sections]
        assert headings == ["Главное", "Сильные стороны", "Слабые стороны", "Основные показатели"]
        rows = document.sections[-1].tables[0].rows
        assert rows
        assert all(row["standing"] for row in rows)
        assert any(line.startswith("Население — ") for line in document.sections[0].paragraphs)
