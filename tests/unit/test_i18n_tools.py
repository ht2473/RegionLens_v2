"""
Проверки сборщика каталога сообщений: идентификатор строится так же, как у Django при выводе.

Расхождение не даёт сбоя — перевод молча не применяется (так было со знаком процента).
"""

from __future__ import annotations

import pytest
from django.template import Context, Template
from django.utils import translation

from scripts.i18n_tools import BASE_DIR, block_message, escape_percent, extract_from_template

pytestmark = pytest.mark.unit


class TestEscapePercent:
    """Удвоение знака процента."""

    def test_percent_is_doubled(self) -> None:
        """Идентификатор сообщения хранит процент удвоенным."""
        assert escape_percent("не менее 80 % субъектов") == "не менее 80 %% субъектов"

    def test_text_without_percent_is_unchanged(self) -> None:
        """Строки без процента остаются как есть."""
        assert escape_percent("Каталог показателей") == "Каталог показателей"


class TestBlockMessage:
    """Идентификатор сообщения блока ``blocktranslate``."""

    def test_substitutions_become_named_fields(self) -> None:
        """Подстановка шаблона превращается в именованное поле формата."""
        assert block_message(" Всего {{ total }} ", "trimmed") == "Всего %(total)s"

    def test_percent_is_doubled_before_substitutions(self) -> None:
        """
        Порядок действий важен.

        При обратном порядке процент подстановки удвоился бы вместе с текстом,
        и вместо значения вывелось бы «%(total)s».
        """
        assert block_message("{{ total }} % итога", "trimmed") == "%(total)s %% итога"


class TestRoundTrip:
    """Совпадение идентификатора с тем, что ищет Django."""

    @pytest.mark.parametrize(
        "source",
        [
            "Показаны ряды с покрытием не менее 80 % субъектов и не менее десяти лет",
            "Показатели",
        ],
    )
    def test_translate_tag_finds_the_translation(self, source: str) -> None:
        """
        Строка, извлечённая сборщиком, находится в каталоге при выводе.

        Проверка идёт через настоящий вывод шаблона, а не через ``gettext``:
        именно на пути «тег → идентификатор» и возникало расхождение.
        """
        template = Template("{% load i18n %}" + '{% translate "' + source + '" %}')
        with translation.override("en"):
            rendered = template.render(Context({}))
        assert rendered != source, "перевод не применился: идентификаторы разошлись"

    def test_block_tag_finds_the_translation(self) -> None:
        """То же для блока с подстановкой и знаком процента."""
        # Строка шапки полного перечня рядов: две подстановки и знак процента.
        template = Template(
            "{% load i18n %}"
            "{% blocktranslate trimmed with total=2952 ready=575 %}"
            "{{ total }} рядов: сборник Росстата, ряды Банка России и ФНС. "
            "{{ ready }} из них пригодны для сравнения регионов: "
            "покрывают не менее 80 % субъектов и не менее десяти лет."
            "{% endblocktranslate %}"
        )
        with translation.override("en"):
            rendered = template.render(Context({}))
        assert "575 of them are fit for comparing regions" in rendered
        assert "80 %" in rendered


class TestTagConstants:
    """Строки ``_("…")`` в аргументах тегов."""

    def test_include_argument_is_collected(self) -> None:
        """Подпись, переданная в ``include … with``, попадает в каталог."""
        source = '{% include "x.html" with geo_caption=_("Карта регионов России") %}'
        found = extract_from_template(source, BASE_DIR / "templates" / "example.html")
        assert ("Карта регионов России", "templates/example.html:1") in found

    def test_percent_is_doubled(self) -> None:
        """Процент в такой строке удваивается, как у тега перевода."""
        source = '{% include "x.html" with title=_("80 % субъектов") %}'
        found = extract_from_template(source, BASE_DIR / "templates" / "example.html")
        assert found == [("80 %% субъектов", "templates/example.html:1")]
