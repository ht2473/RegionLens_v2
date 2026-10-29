"""Фильтры шаблонов своих данных: язык текста таблицы на странице другого языка."""

from __future__ import annotations

from django.utils import translation

from apps.userdata.templatetags.userdata import in_source_lang, source_lang


def test_russian_text_on_english_page_is_marked() -> None:
    with translation.override("en"):
        assert source_lang("Белгородская область") == "ru"
        assert in_source_lang("Столбец <ОКТМО>") == ('<span lang="ru">Столбец &lt;ОКТМО&gt;</span>')


def test_russian_page_needs_no_mark() -> None:
    with translation.override("ru"):
        assert source_lang("Белгородская область") == ""
        assert in_source_lang("Столбец <ОКТМО>") == "Столбец &lt;ОКТМО&gt;"
