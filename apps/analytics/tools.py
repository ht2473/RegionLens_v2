"""Перечень аналитических инструментов с вопросом, на который отвечает каждый."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.urls import reverse
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True, slots=True)
class Tool:
    """Описание одного аналитического инструмента."""

    code: str
    url_name: str
    title: Any
    question: Any
    summary: Any
    methods: Any
    icon: str = "activity"
    # Код инструмента, разбор которого этот продолжает; отдельной карточки у него нет.
    parent: str = ""

    @property
    def url(self) -> str:
        """Адрес страницы инструмента."""
        return reverse(f"analytics:{self.url_name}")

    @property
    def children(self) -> tuple[Tool, ...]:
        """Инструменты, продолжающие разбор этого."""
        return tuple(tool for tool in TOOLS if tool.parent == self.code)


TOOLS: tuple[Tool, ...] = (
    Tool(
        code="inequality",
        url_name="inequality",
        title=_("Неравенство"),
        question=_("Насколько сильно различаются регионы и меняется ли различие со временем?"),
        summary=_(
            "Набор мер межрегионального различия с кривой Лоренца и разложением "
            "индекса Тейла на составляющие «между округами» и «внутри округов»."
        ),
        methods=_("Джини · Тейл · Аткинсон · коэффициент вариации · децильный коэффициент"),
        icon="inequality",
    ),
    Tool(
        code="convergence",
        url_name="convergence",
        title=_("Конвергенция"),
        question=_("Догоняют ли отстающие регионы передовые?"),
        summary=_(
            "Проверка сближения двумя независимыми способами: по сокращению разброса "
            "и по связи темпа роста с исходным уровнем. Период полусокращения разрыва."
        ),
        methods=_("сигма-конвергенция · абсолютная и условная бета-конвергенция"),
        icon="convergence",
        parent="inequality",
    ),
    Tool(
        code="correlation",
        url_name="correlation",
        title=_("Корреляции"),
        question=_("Какие показатели связаны между собой и насколько надёжно?"),
        summary=_(
            "Матрица парных связей с проверкой значимости, частная корреляция "
            "при исключённом влиянии остальных показателей и проверка запаздывания."
        ),
        methods=_("Пирсон · Спирмен · Кендалл · частная корреляция · регрессия"),
        icon="correlation",
    ),
    Tool(
        code="spatial",
        url_name="spatial",
        title=_("Пространственный анализ"),
        question=_("Группируются ли похожие значения в пространстве?"),
        summary=_(
            "Проверка того, объясняется ли картина показателя географией: "
            "глобальный индекс Морана и карта локальных пространственных кластеров."
        ),
        methods=_("индекс Морана · LISA · матрица соседства · перестановочные оценки"),
        icon="spatial",
    ),
    Tool(
        code="index-builder",
        url_name="index-builder",
        title=_("Интегральные индексы"),
        question=_("Как собрать собственную сводную оценку регионов и устойчива ли она?"),
        summary=_(
            "Конструктор составного показателя: выбор рядов, приведение к общей шкале, "
            "назначение весов, свёртка и проверка устойчивости итогового рейтинга."
        ),
        methods=_("5 способов нормализации · 5 способов весов · 4 схемы свёртки"),
        icon="index",
    ),
    Tool(
        code="revisions",
        url_name="revisions",
        title=_("Пересмотры статистики"),
        question=_("Насколько менялись опубликованные значения между выпусками сборников?"),
        summary=_(
            "Разбор расхождений между изданиями: какие разделы и ряды пересматриваются "
            "чаще всего, насколько сильно и за какие годы."
        ),
        methods=_("сопоставление версий наблюдений по выпускам изданий"),
        icon="revisions",
    ),
)


TOOLS_BY_CODE: dict[str, Tool] = {tool.code: tool for tool in TOOLS}

# Инструменты, показываемые в перечне раздела отдельными карточками.
SECTION_TOOLS: tuple[Tool, ...] = tuple(tool for tool in TOOLS if not tool.parent)
