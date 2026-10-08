"""Проверки методики и глоссария, прежде всего правил видимости неопубликованного."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.content.constants import MethodologyBlock
from apps.content.models import GlossaryTerm, MethodologySection
from apps.content.selectors import alphabet_index, methodology_blocks, terms_by_letter

pytestmark = pytest.mark.integration


@pytest.fixture
def term(db: None) -> GlossaryTerm:
    """Опубликованный термин глоссария."""
    item = GlossaryTerm.objects.create(slug="test-term")
    item.set_current_language("ru")
    item.term = "Дисперсия"
    item.short_definition = "Мера разброса значений"
    item.definition = "Средний квадрат отклонения от среднего"
    item.synonyms = "variance"
    item.save()
    return item


@pytest.fixture
def section(db: None) -> MethodologySection:
    """Раздел методологии, связанный со страницей неравенства."""
    item = MethodologySection.objects.create(
        code="test-inequality",
        block=MethodologyBlock.INEQUALITY,
        formula="G = 1 - 2B",
        tool_url_name="analytics:inequality",
    )
    item.set_current_language("ru")
    item.title = "Проверочный раздел"
    item.summary = "Краткое описание"
    item.body = "Изложение метода"
    item.save()
    return item


# ---------------------------------------------------------------------------------------
# Глоссарий
# ---------------------------------------------------------------------------------------


def test_glossary_shows_published_terms(client: Client, term: GlossaryTerm) -> None:
    """Термин появляется на странице глоссария вместе с определением."""
    response = client.get(reverse("content:glossary"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Дисперсия" in body
    assert "Мера разброса значений" in body


def test_unpublished_term_is_not_shown(client: Client, term: GlossaryTerm) -> None:
    """Скрытый термин не попадает в глоссарий."""
    term.is_published = False
    term.save(update_fields=["is_published"])

    body = client.get(reverse("content:glossary")).content.decode()

    assert "Дисперсия" not in body


def test_glossary_search_finds_by_synonym(client: Client, term: GlossaryTerm) -> None:
    """
    Поиск идёт и по синонимам.

    Пользователь ищет слово, встреченное в тексте, и оно может оказаться синонимом
    заголовка статьи словаря.
    """
    response = client.get(reverse("content:glossary"), {"q": "variance"})
    assert "Дисперсия" in response.content.decode()


def test_term_permalink_redirects_to_anchor(client: Client, term: GlossaryTerm) -> None:
    """Постоянный адрес термина переводит на глоссарий к нужному якорю."""
    response = client.get(reverse("content:term", kwargs={"slug": term.slug}))
    assert response.status_code == 302
    assert response["Location"].endswith(f"#{term.anchor}")


def test_alphabet_index_includes_letters_of_untranslated_terms(term: GlossaryTerm) -> None:
    """
    В указателе есть буквы, которых нет в алфавите текущего языка.

    У термина может не быть английского перевода: он показывается по-русски
    и попадает под букву кириллицы. Без неё в указателе термин был бы недостижим.
    """
    from django.utils import translation

    with translation.override("en"):
        groups = terms_by_letter()
        index = alphabet_index(groups)

    letters = {entry["letter"] for entry in index if entry["available"]}
    assert "Д" in letters


# ---------------------------------------------------------------------------------------
# Методология
# ---------------------------------------------------------------------------------------


def test_methodology_groups_sections_by_block(client: Client, section: MethodologySection) -> None:
    """Разделы методологии сгруппированы по блокам; пустые блоки не показываются."""
    response = client.get(reverse("content:methodology"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Проверочный раздел" in body
    assert "G = 1 - 2B" in body

    blocks = methodology_blocks()
    assert all(block["sections"] for block in blocks)


def test_methodology_link_appears_on_tool_page(
    client: Client, section: MethodologySection, reference_seed: None
) -> None:
    """
    Со страницы инструмента ведёт ссылка на описание его метода.

    Методику читают в момент, когда смотрят на результат: ссылка на неё должна быть
    на самой странице расчёта, а не только в общем разделе.
    """
    response = client.get(reverse("analytics:inequality"))
    body = response.content.decode()

    assert response.status_code == 200
    assert f"#{section.anchor}" in body


def test_unpublished_section_is_not_shown(client: Client, section: MethodologySection) -> None:
    """Снятый с показа раздел методологии не появляется на странице."""
    section.is_published = False
    section.save(update_fields=["is_published"])

    response = client.get(reverse("content:methodology"))
    assert "Проверочный раздел" not in response.content.decode()


def test_section_shows_mathml_and_limitations(client: Client, section: MethodologySection) -> None:
    """
    Формула выводится разметкой MathML с подписью, ограничения — списком.

    Разметку правит и редактор в панели: на страницу попадает только белый список.
    """
    section.formula = 'Джини: <math display="block"><mi>G</mi><script>alert(1)</script></math>'
    section.limitations = "Первое ограничение.\nВторое ограничение."
    section.save()

    body = client.get(reverse("content:methodology")).content.decode()

    assert '<math display="block"><mi>G</mi></math>' in body
    assert "alert(1)" not in body
    assert "methodology__formula-label" in body
    assert "<li>Первое ограничение.</li><li>Второе ограничение.</li>" in body


def test_seed_retires_merged_sections(db: None) -> None:
    """Разделы прежних редакций снимаются при наполнении: на странице их нет."""
    from django.core.management import call_command

    from apps.content.management.commands.seed_content import RETIRED_SECTIONS

    old = MethodologySection.objects.create(code=RETIRED_SECTIONS[0])
    old.set_current_language("ru")
    old.title = "Прежний раздел"
    old.save()

    call_command("seed_content", verbosity=0)

    assert not MethodologySection.objects.filter(code__in=RETIRED_SECTIONS).exists()


def test_methodology_examples_use_current_data(client: Client, warehouse: Any) -> None:
    """
    Примеры к разделам считаются по нынешним данным склада.

    Число в статичном тексте разошлось бы с сайтом после первой же новой сборки.
    """
    from django.core.management import call_command

    call_command("seed_content", verbosity=0)
    response = client.get(reverse("content:methodology"))
    sections = {
        section.code: section
        for block in response.context["blocks"]
        for section in block["sections"]
    }

    assert sections["inequality"].example is not None
    assert "Джини" in sections["inequality"].example.lines[0]
    assert sections["map-classes"].example is not None
    assert "Пример" in response.content.decode()


def test_methodology_without_warehouse_has_no_examples(client: Client, section: Any) -> None:
    """Без склада страница открывается, примеров у разделов нет."""
    response = client.get(reverse("content:methodology"))

    assert response.status_code == 200
    assert all(
        item.example is None for block in response.context["blocks"] for item in block["sections"]
    )


# ---------------------------------------------------------------------------------------
# Термины в интерфейсе
# ---------------------------------------------------------------------------------------


def test_term_tag_shows_definition_by_click(rf: Any, term: GlossaryTerm) -> None:
    """Термин — кнопка с определением из глоссария во всплывающей панели и ссылкой."""
    from django.template import Context, Template

    rendered = Template(
        '{% load glossary %}{% term "test-term" %}, {% term "test-term" "D" %}'
    ).render(Context({"request": rf.get("/")}))

    assert rendered.count('class="term__name"') == 2
    assert ">Дисперсия</button>" in rendered
    assert ">D</button>" in rendered
    assert "Мера разброса значений" in rendered
    assert f"#{term.anchor}" in rendered
    assert "</span></span>, " in rendered


def test_unknown_or_hidden_term_is_plain_text(rf: Any, term: GlossaryTerm) -> None:
    """Термина нет или он снят с показа — остаётся подпись без кнопки."""
    from django.template import Context, Template

    term.is_published = False
    term.save(update_fields=["is_published"])
    rendered = Template(
        '{% load glossary %}{% term "test-term" "Дисперсия" %}|{% term "nope" %}'
    ).render(Context({"request": rf.get("/")}))

    assert rendered == "Дисперсия|nope"


# ---------------------------------------------------------------------------------------
# Двуязычие
# ---------------------------------------------------------------------------------------


def test_content_falls_back_to_russian(client: Client, term: GlossaryTerm) -> None:
    """
    Термин без английской версии показывается по-русски, а не пропадает.

    Проверяется признак модели, а не формулировка пометки.
    """
    response = client.get("/en/glossary/")

    assert response.status_code == 200
    assert "Дисперсия" in response.content.decode()
    assert term.shown_in_original is True


def test_english_translation_is_used_when_present(client: Client, term: GlossaryTerm) -> None:
    """Если английская версия заполнена, показывается именно она."""
    term.set_current_language("en")
    term.term = "Variance"
    term.short_definition = "A measure of spread"
    term.definition = "Mean squared deviation from the mean"
    term.save()

    body = client.get("/en/glossary/").content.decode()

    assert "Variance" in body
    assert "Мера разброса значений" not in body


def test_russian_synonyms_are_marked_on_english_page(client: Client, term: GlossaryTerm) -> None:
    """Русский синоним в английской версии остаётся для поиска и помечен русским языком."""
    term.set_current_language("en")
    term.term = "Variance"
    term.short_definition = "A measure of spread"
    term.synonyms = "dispersion, дисперсия"
    term.save()

    body = client.get("/en/glossary/").content.decode()

    assert '<span lang="en">dispersion</span>, <span lang="ru">дисперсия</span>' in body


def test_language_switch_preserves_query_string(client: Client, db: None) -> None:
    """
    Переключение языка сохраняет параметры страницы.

    Иначе смена языка на странице расчёта сбрасывала бы выбранные ряды и годы —
    и переключатель становился бы кнопкой «начать заново».
    """
    response = client.get(reverse("content:glossary"), {"q": "дисперсия"})
    body = response.content.decode()

    assert "/en/glossary/?q=" in body


def test_alternate_links_are_declared(client: Client, db: None) -> None:
    """У страницы объявлены обе языковые версии ссылками hreflang."""
    response = client.get(reverse("content:glossary"))
    body = response.content.decode()

    assert 'hreflang="ru"' in body
    assert 'hreflang="en"' in body
    assert 'hreflang="x-default"' in body
    assert 'rel="canonical"' in body


def test_sitemap_lists_both_languages(client: Client, db: None) -> None:
    """Карта сайта содержит обе языковые версии страниц."""
    response = client.get("/sitemap.xml")
    body = response.content.decode()

    assert response.status_code == 200
    assert "/ru/methodology/" in body
    assert "/en/methodology/" in body


def test_robots_closes_private_sections(client: Client, db: None) -> None:
    """Правила обхода закрывают кабинет и панель управления."""
    response = client.get("/robots.txt")
    body = response.content.decode()

    assert response.status_code == 200
    assert "Disallow: /ru/cabinet/" in body
    assert "Disallow: /ru/manage/" in body
    assert "Sitemap:" in body


def test_term_form_names_empty_choice_in_page_language(db: None) -> None:
    """Пустой вариант связанных показателя и раздела подписан проектом, а не строкой Django."""
    from django.utils import translation

    from apps.content.forms import GlossaryTermForm

    with translation.override("ru"):
        markup = str(GlossaryTermForm())
    assert "Select an option" not in markup
    assert markup.count(">не выбран<") == 2
