"""Переносы печатной вёрстки в названиях разрезов: исправление, ключ ряда, английское название."""

from __future__ import annotations

from apps.warehouse.etl.catalog_names import catalog_names
from apps.warehouse.etl.classify import make_series_key
from apps.warehouse.etl.dimensions import _clean_subsection
from apps.warehouse.etl.typography import fix_subsection


def test_hyphenation_is_removed() -> None:
    assert fix_subsection("Рентге-нолабо-ранты") == "Рентгенолаборанты"
    assert fix_subsection("В том числе: Автомобиль-ный транспорт") == (
        "В том числе: Автомобильный транспорт"
    )


def test_latin_letter_is_replaced() -> None:
    """Латинская c в слове, набранном кириллицей."""
    assert fix_subsection("Сельcкохо-зяйственные организации") == (
        "Сельскохозяйственные организации"
    )


def test_compound_words_are_kept() -> None:
    for text in ("жилищно-коммунальные услуги", "дети-инвалиды", "договоры купли-продажи"):
        assert fix_subsection(text) == text


def test_variants_of_one_subsection_share_a_key() -> None:
    """Один разрез с разными переносами в разных выпусках — один ряд."""
    keys = {
        make_series_key("Y477000001", _clean_subsection(text))
        for text in ("Рентге-нолабо-ранты", "Рентгено-лаборанты", "Рентгенолаборанты")
    }
    assert len(keys) == 1


def test_stub_is_no_subsection() -> None:
    assert _clean_subsection("CD") is None
    assert _clean_subsection("") is None


def test_translations_follow_fixed_names() -> None:
    """Английские названия разрезов ищутся по исправленному тексту."""
    names = catalog_names()["subsections"]
    assert not any("-нолабо-" in name or "Автомобиль-ный" in name for name in names)
