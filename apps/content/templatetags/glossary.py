"""
Термины в интерфейсе и формулы методики.

``{% term "gini" %}`` — название термина, по нажатию — краткое определение из глоссария
и ссылки в глоссарий и методику; ``{% term "gini" label %}`` — со своей подписью. Термина
нет или он снят с показа — выводится одна подпись. ``section.formula|formula_lines`` —
строки формулы раздела с очищенной разметкой MathML.
"""

from __future__ import annotations

import secrets
from typing import Any

from django import template
from django.template.loader import render_to_string
from django.utils.html import conditional_escape
from django.utils.safestring import SafeString, mark_safe

from apps.content.mathml import FormulaLine
from apps.content.mathml import formula_lines as split_formula
from apps.content.selectors import term_cards

register = template.Library()


@register.simple_tag(takes_context=True)
def term(context: template.Context, slug: str, label: Any = None) -> SafeString:
    """Подпись термина с определением по нажатию."""
    card = term_cards(context.get("request")).get(slug)
    text = label if label not in (None, "") else (card.term if card else slug)
    if card is None:
        return conditional_escape(text)
    rendered = render_to_string(
        "partials/_term.html",
        {"card": card, "label": text, "panel_id": f"term-{slug}-{secrets.token_hex(3)}"},
    )
    # Без перевода строки в конце: термин стоит внутри фразы перед знаком препинания.
    return mark_safe(rendered.strip())  # noqa: S308  # nosec B308 B703 - отрисовано шаблоном


@register.filter
def formula_lines(text: str) -> list[FormulaLine]:
    """Строки поля формулы: подпись и MathML или текст."""
    return split_formula(text)
