"""Язык текстов источника: непереведённое примечание на английской странице помечается русским."""

from __future__ import annotations

import pytest
from django.utils import translation

from apps.catalog.models import MethodologyNote, SeriesBreak

pytestmark = pytest.mark.unit


class TestNoteLanguage:
    """Примечание источника."""

    def test_untranslated_note_is_russian_on_english_page(self) -> None:
        """Без перевода текст русский — и язык русский."""
        note = MethodologyNote(text_ru="Данные пересчитаны")
        with translation.override("en"):
            assert note.text == "Данные пересчитаны"
            assert note.text_lang == "ru"

    def test_translated_note_is_english(self) -> None:
        """С переводом — английский."""
        note = MethodologyNote(text_ru="Данные пересчитаны", text_en="Data recalculated")
        with translation.override("en"):
            assert note.text_lang == "en"
        with translation.override("ru"):
            assert note.text_lang == "ru"


class TestBreakLanguage:
    """Пояснение к разрыву."""

    def test_untranslated_description_is_russian(self) -> None:
        """Русское пояснение на английской странице помечено русским."""
        entry = SeriesBreak(year=2017, description_ru="Смена методики")
        with translation.override("en"):
            assert entry.description_lang == "ru"

    def test_translated_description_is_english(self) -> None:
        """Переведённое пояснение — английское."""
        entry = SeriesBreak(year=2017, description_ru="Смена методики", description_en="New method")
        with translation.override("en"):
            assert entry.description == "New method"
            assert entry.description_lang == "en"

    def test_description_from_note_follows_note(self) -> None:
        """Пояснение без своего текста берётся из примечания — и язык его."""
        entry = SeriesBreak(year=2017, note=MethodologyNote(text_ru="Смена", text_en="Change"))
        with translation.override("en"):
            assert entry.description == "Change"
            assert entry.description_lang == "en"
