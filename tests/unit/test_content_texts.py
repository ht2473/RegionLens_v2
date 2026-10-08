"""Тексты методики и глоссария: числовые правила и формулы MathML."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from apps.content import mathml, text_rules
from apps.content.management.commands.seed_content import GLOSSARY, METHODOLOGY

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------------------
# Правила текста
# ---------------------------------------------------------------------------------------


def test_seed_texts_follow_rules() -> None:
    """
    Наполнение методики и глоссария проходит числовые правила.

    Правило без проверки расходится с текстом с первой же правки: «Подробнее» снова
    растёт, предложения склеиваются точкой с запятой.
    """
    problems = text_rules.report(METHODOLOGY, GLOSSARY)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Разрыв ставится с 2017 г. по новой методике. Так в примечании.", 2),
        ("Допуск — 0,15 п. п. Для итогов он другой.", 2),
        ("Индексы реальных доходов I. ВРП пересчитывается иначе.", 2),
        ("Сборник «Регионы России» 2023 года. Выпуск следующий.", 2),
        ("Первый абзац.\n\nВторой абзац без точки", 2),
    ],
)
def test_sentences_split_at_real_ends(text: str, expected: int) -> None:
    """Сокращения внутри предложения его не обрывают, конец фразы на «п. п.» — обрывает."""
    assert len(text_rules.sentences(text)) == expected


def test_rules_catch_chains_and_length() -> None:
    """Точка с запятой, два тире в предложении и лишние слова — нарушения."""
    long_body = " ".join(["Слово"] * 130) + "."
    problems = text_rules.prose_problems(
        f"Одно — другое — третье. Первое; второе. {long_body}", limit=120
    )
    text = " ".join(problems)
    assert "точек с запятой" in text
    assert "несколькими тире" in text
    assert "предел 120" in text
    assert "предложение в" in text


def test_section_needs_two_or_three_limitations() -> None:
    """Ограничений у раздела — два-три, по одному предложению."""
    item = {"code": "x", "summary": "Одно предложение.", "body": "", "limitations": "Одно."}
    assert any("ограничений: 1" in p for p in text_rules.section_problems(item))


# ---------------------------------------------------------------------------------------
# Формулы MathML
# ---------------------------------------------------------------------------------------


def test_cleaning_drops_markup_outside_white_list() -> None:
    """Сценарии, обработчики и чужие атрибуты вырезаются, текст экранируется."""
    dirty = (
        '<math display="block" onload="alert(1)"><mi style="color:red">x</mi>'
        "<script>alert(2)</script><mtext>&lt;img src=x onerror=alert(3)&gt;</mtext>"
        '<a href="javascript:alert(4)">ссылка</a><mspace width="1em"/></math>'
    )
    clean = str(mathml.clean(dirty))

    assert "onload" not in clean
    assert "style" not in clean
    assert "<script" not in clean
    assert "alert(2)" not in clean
    assert "<a " not in clean
    assert "<img" not in clean
    assert "&lt;img" in clean
    assert clean.startswith('<math display="block">')
    assert '<mspace width="1em"></mspace>' in clean
    ET.fromstring(clean)


def test_cleaning_closes_open_elements() -> None:
    """Незакрытые элементы закрываются по порядку: разметка страницы не ломается."""
    clean = str(mathml.clean("<math><mrow><mi>x"))
    assert clean == "<math><mrow><mi>x</mi></mrow></math>"


def test_formula_lines_split_label_and_math() -> None:
    """Строка поля — подпись до «<math» и формула; строка без MathML — текст."""
    lines = mathml.formula_lines("Джини: <math><mi>G</mi></math>\nG = 1 - 2B")

    assert lines[0].label == "Джини"
    assert str(lines[0].math) == "<math><mi>G</mi></math>"
    assert lines[1].math is None
    assert lines[1].text == "G = 1 - 2B"


def test_seed_formulas_are_well_formed_and_clean() -> None:
    """Формулы наполнения разбираются как XML и проходят белый список без потерь."""
    for item in METHODOLOGY:
        for line in mathml.formula_lines(item.get("formula", "")):
            assert line.math is not None, item["code"]
            ET.fromstring(str(line.math))
            assert str(line.math) in item["formula"], item["code"]
