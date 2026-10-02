"""
Разделы методики и термины глоссария.

Переводы — в отдельных таблицах django-parler: записи правятся по одной, языки ведутся
независимо.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models
from django.urls import NoReverseMatch, reverse
from django.utils.translation import get_language
from parler.models import TranslatableModel, TranslatedFields

from apps.core.models import OrderedModel, TimeStampedModel
from apps.core.translation_qa import CYRILLIC

from .constants import GlossaryCategory, MethodologyBlock


class PublishedQuerySet(models.QuerySet):
    """Выборка содержимого с фильтром по признаку показа на сайте."""

    def published(self) -> PublishedQuerySet:
        """Только записи, которые показываются на сайте."""
        return self.filter(is_published=True)


class OriginalLanguageMixin(models.Model):
    """Признак того, что запись показывается не на запрошенном языке, а в оригинале."""

    if TYPE_CHECKING:  # pragma: no cover - объявление только для проверки типов
        # Метод TranslatableModel; наследование от неё дало бы вторую таблицу переводов.
        def get_available_languages(self) -> list[str]: ...

    class Meta:
        abstract = True

    @property
    def shown_in_original(self) -> bool:
        """Признак показа записи на языке оригинала вместо запрошенного."""
        requested = get_language() or ""
        return bool(requested) and requested not in self.get_available_languages()


# ---------------------------------------------------------------------------------------
# Методология
# ---------------------------------------------------------------------------------------


# Менеджер — от django-parler без сведений о типах.
class MethodologySection(  # type: ignore[django-manager-missing]
    TranslatableModel, OriginalLanguageMixin, OrderedModel, TimeStampedModel
):
    """
    Раздел страницы «Методика»: формула, условия применимости и ограничения метода.

    Связан с инструментом анализа именем маршрута: ссылки ведут в обе стороны.
    """

    code = models.SlugField(
        "код раздела",
        max_length=60,
        unique=True,
        help_text="Устойчивый идентификатор для ссылок со страниц анализа",
    )
    block = models.CharField(
        "блок",
        max_length=20,
        choices=MethodologyBlock.choices,
        default=MethodologyBlock.STATISTICS,
        db_index=True,
    )
    tool_url_name = models.CharField(
        "маршрут инструмента",
        max_length=80,
        blank=True,
        help_text="Имя маршрута страницы анализа, например analytics:inequality",
    )
    references = models.JSONField(
        "источники",
        default=list,
        blank=True,
        help_text="Библиографические ссылки, обосновывающие метод",
    )
    is_published = models.BooleanField("показывать на сайте", default=True, db_index=True)

    translations = TranslatedFields(
        title=models.CharField("заголовок", max_length=200),
        summary=models.CharField(
            "краткое описание",
            max_length=400,
            blank=True,
            help_text="Одно предложение о том, что даёт метод",
        ),
        # Переводится: в записи формулы есть слова («доля = число регионов …»).
        formula=models.CharField(
            "формула",
            max_length=300,
            blank=True,
            help_text="Запись формулы в текстовом виде; показывается моноширинным шрифтом",
        ),
        body=models.TextField(
            "изложение",
            blank=True,
            help_text="Обозначения, условия применимости, ограничения метода",
        ),
    )

    class Meta:
        verbose_name = "раздел методологии"
        verbose_name_plural = "разделы методологии"
        ordering = ["block", "display_order", "code"]
        indexes = [
            models.Index(fields=["block", "display_order"], name="methodology_block_order_idx"),
        ]

    def __str__(self) -> str:
        return self.safe_translation_getter("title", any_language=True) or self.code

    @property
    def references_with_lang(self) -> list[tuple[str, str]]:
        """Библиографические ссылки с языком: русские издания цитируются по-русски."""
        return [
            (reference, "ru" if CYRILLIC.search(reference) else "en")
            for reference in self.references or []
        ]

    @property
    def anchor(self) -> str:
        """Якорь раздела на странице методологии."""
        return f"m-{self.code}"

    @property
    def tool_url(self) -> str:
        """Адрес страницы анализа, где применяется метод; пусто — метод общий."""
        if not self.tool_url_name:
            return ""
        try:
            return reverse(self.tool_url_name)
        except NoReverseMatch:  # pragma: no cover - возможен лишь при удалении маршрута
            return ""


# ---------------------------------------------------------------------------------------
# Глоссарий
# ---------------------------------------------------------------------------------------


class GlossaryTerm(TranslatableModel, OriginalLanguageMixin, TimeStampedModel):
    """Термин глоссария, связанный с показателем каталога и разделом методики."""

    slug = models.SlugField("адрес", max_length=120, unique=True)
    category = models.CharField(
        "раздел",
        max_length=20,
        choices=GlossaryCategory.choices,
        default=GlossaryCategory.STATISTICS,
        db_index=True,
    )
    is_published = models.BooleanField("показывать на сайте", default=True, db_index=True)
    indicator = models.ForeignKey(
        "catalog.Indicator",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="glossary_terms",
        verbose_name="показатель каталога",
    )
    methodology_section = models.ForeignKey(
        MethodologySection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="terms",
        verbose_name="раздел методологии",
    )
    related_terms = models.ManyToManyField(
        "self",
        blank=True,
        symmetrical=True,
        verbose_name="смежные термины",
    )

    translations = TranslatedFields(
        term=models.CharField("термин", max_length=200, db_index=True),
        short_definition=models.CharField(
            "краткое определение",
            max_length=300,
            help_text="Одно предложение — показывается во всплывающей подсказке",
        ),
        definition=models.TextField("определение", blank=True),
        synonyms=models.CharField(
            "синонимы",
            max_length=300,
            blank=True,
            help_text="Через запятую; используются поиском по глоссарию",
        ),
    )

    objects = PublishedQuerySet.as_manager()

    class Meta:
        verbose_name = "термин глоссария"
        verbose_name_plural = "глоссарий"
        ordering = ["slug"]

    def __str__(self) -> str:
        return self.safe_translation_getter("term", any_language=True) or self.slug

    def get_absolute_url(self) -> str:
        """Адрес термина: якорь на общей странице глоссария."""
        return f"{reverse('content:glossary')}#{self.anchor}"

    @property
    def anchor(self) -> str:
        """Якорь термина на странице глоссария."""
        return f"t-{self.slug}"

    @property
    def letter(self) -> str:
        """Первая буква термина на текущем языке."""
        term = self.safe_translation_getter("term", any_language=True) or self.slug
        return term.strip()[:1].upper()

    @property
    def synonyms_with_lang(self) -> list[tuple[str, str]]:
        """Синонимы по одному с языком: русские остаются и в английской версии для поиска."""
        synonyms = self.safe_translation_getter("synonyms", default="") or ""
        return [
            (synonym, "ru" if CYRILLIC.search(synonym) else "en")
            for synonym in (part.strip() for part in synonyms.split(","))
            if synonym
        ]
