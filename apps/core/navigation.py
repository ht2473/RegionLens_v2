"""Структура меню (шапка, подвал, быстрый переход) и «хлебные крошки»."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.search import normalize, terms

if TYPE_CHECKING:  # pragma: no cover - только для проверки типов
    from django.utils.functional import _StrOrPromise as Translatable
else:
    # Во время выполнения ленивый перевод приводится к строке при обращении.
    Translatable = str


@dataclass(frozen=True, slots=True)
class NavItem:
    """Пункт навигации."""

    title: Translatable
    url_name: str
    icon: str = ""
    hint: Translatable = ""
    children: tuple[NavItem, ...] = field(default_factory=tuple)
    # Адреса, которые пункт представляет, не показывая в меню: на них пункт отмечен
    # открытым, а быстрый переход находит их по названию.
    includes: tuple[NavItem, ...] = field(default_factory=tuple)
    # Подпись ссылки на страницу раздела в раскрытой панели.
    overview: Translatable = ""

    def resolve(self) -> str:
        """Получить адрес пункта; ошибка в имени маршрута поднимает исключение."""
        return reverse(self.url_name)

    @property
    def has_children(self) -> bool:
        """Признак наличия вложенных пунктов."""
        return bool(self.children)


# ---------------------------------------------------------------------------------------
# Основное меню: «Исследовать» и «Анализ» — входы в работу; показатели, регионы
# и методика — справочное. «Данные», «API» и «О проекте» — в подвале.
# ---------------------------------------------------------------------------------------

EXPLORE_VIEWS: tuple[NavItem, ...] = (
    NavItem(
        title=_("Карта"),
        url_name="maps:choropleth",
        hint=_("Картограмма с выбором способа разбиения шкалы и года"),
    ),
    NavItem(
        title=_("Динамика"),
        url_name="compare:index",
        hint=_("Линии по выбранным территориям, профиль и таблица различий"),
    ),
    NavItem(
        title=_("Рейтинг"),
        url_name="rankings:index",
        hint=_("Позиции регионов и их изменение год к году"),
    ),
    NavItem(
        title=_("Распределение"),
        url_name="surface:distribution",
        hint=_("Разброс значений по субъектам: гистограмма, квартили, положение"),
    ),
    NavItem(
        title=_("Таблица"),
        url_name="surface:table",
        hint=_("Все субъекты подряд, включая те, по которым значения нет"),
    ),
)

MAIN_NAVIGATION: tuple[NavItem, ...] = (
    NavItem(
        title=_("Регионы"),
        url_name="catalog:territory-list",
        icon="map-pin",
        hint=_("85 субъектов Российской Федерации: паспорт, ранги, динамика"),
    ),
    NavItem(
        title=_("Показатели"),
        url_name="catalog:indicator-list",
        icon="list",
        hint=_("Каталог показателей с фильтрами по разделу, покрытию и источнику"),
    ),
    NavItem(
        title=_("Исследовать"),
        url_name="maps:choropleth",
        icon="map",
        hint=_(
            "Показатель, территории и год на одном экране: карта, динамика, рейтинг, "
            "распределение и таблица"
        ),
        includes=EXPLORE_VIEWS,
    ),
    NavItem(
        title=_("Анализ"),
        url_name="analytics:index",
        icon="activity",
        hint=_("Инструменты сравнительного и пространственного анализа"),
        overview=_("Все инструменты анализа"),
        # Конвергенции нет в меню, но быстрый переход находит её по названию.
        includes=(
            NavItem(
                title=_("Конвергенция"),
                url_name="analytics:convergence",
                hint=_("Сближение регионов: сигма- и бета-конвергенция"),
            ),
        ),
        children=(
            NavItem(
                title=_("Интегральные индексы"),
                url_name="analytics:index-builder",
                hint=_("Собственная методика оценки: показатели, нормализация, веса"),
            ),
            NavItem(
                title=_("Неравенство"),
                url_name="analytics:inequality",
                hint=_("Джини, Тейл с декомпозицией по округам, кривая Лоренца"),
            ),
            NavItem(
                title=_("Корреляции"),
                url_name="analytics:correlation",
                hint=_("Связи между показателями с проверкой значимости"),
            ),
            NavItem(
                title=_("Пространственный анализ"),
                url_name="analytics:spatial",
                hint=_("Индекс Морана и локальные пространственные кластеры"),
            ),
            NavItem(
                title=_("Пересмотры статистики"),
                url_name="analytics:revisions",
                hint=_("Как менялись значения между выпусками сборников"),
            ),
        ),
    ),
    NavItem(
        title=_("Методика"),
        url_name="content:methodology",
        icon="book",
        hint=_("Формулы и правила, по которым выполняются расчёты"),
    ),
)


# Разделы, показанные только в подвале; быстрый переход находит их по названию.
SECONDARY_SECTIONS: tuple[NavItem, ...] = (
    NavItem(
        title=_("Данные"),
        url_name="catalog:dataset",
        icon="database",
        hint=_("Состав набора, покрытие, качество и выгрузка"),
    ),
    NavItem(
        title=_("API"),
        url_name="api:docs",
        icon="code",
        hint=_("Программный доступ к данным по протоколу REST"),
    ),
    NavItem(
        title=_("О проекте"),
        url_name="core:about",
        icon="info",
        hint=_("Назначение, с чего начать, обозначения, источник данных и автор"),
    ),
    NavItem(
        title=_("Условия использования"),
        url_name="core:terms",
        icon="file-text",
        hint=_("Условия источников, расчёты, программный интерфейс, учётная запись"),
    ),
    NavItem(
        title=_("Политика обработки персональных данных"),
        url_name="core:privacy",
        icon="file-text",
        hint=_("Какие сведения о пользователях хранятся, зачем, как долго и как их удалить"),
    ),
)

# Все разделы ресурса: шапка и вынесенные из неё.
SITE_SECTIONS: tuple[NavItem, ...] = (*MAIN_NAVIGATION, *SECONDARY_SECTIONS)


# Пункты подвала, сгруппированные по смыслу.
FOOTER_NAVIGATION: tuple[tuple[Translatable, tuple[NavItem, ...]], ...] = (
    (
        _("Данные"),
        (
            NavItem(title=_("Каталог показателей"), url_name="catalog:indicator-list"),
            NavItem(title=_("Регионы"), url_name="catalog:territory-list"),
            NavItem(title=_("О наборе данных"), url_name="catalog:dataset"),
            NavItem(title=_("Источники данных"), url_name="catalog:sources"),
        ),
    ),
    (
        _("Анализ"),
        (
            NavItem(title=_("Карта"), url_name="maps:choropleth"),
            NavItem(title=_("Рейтинг"), url_name="rankings:index"),
            NavItem(title=_("Динамика и сравнение"), url_name="compare:index"),
            NavItem(title=_("Распределение по субъектам"), url_name="surface:distribution"),
            NavItem(title=_("Таблица значений"), url_name="surface:table"),
            NavItem(title=_("Интегральные индексы"), url_name="analytics:index-builder"),
            NavItem(title=_("Неравенство"), url_name="analytics:inequality"),
        ),
    ),
    (
        _("Кабинет и API"),
        (
            NavItem(title=_("Личный кабинет"), url_name="accounts:dashboard"),
            NavItem(title=_("Программный интерфейс"), url_name="api:docs"),
        ),
    ),
    (
        _("Справка"),
        (
            NavItem(title=_("О проекте"), url_name="core:about"),
            NavItem(title=_("Глоссарий"), url_name="content:glossary"),
            NavItem(title=_("Условия использования"), url_name="core:terms"),
            NavItem(title=_("Персональные данные"), url_name="core:privacy"),
            NavItem(title=_("Обратная связь"), url_name="feedback:create"),
        ),
    ),
)


# ---------------------------------------------------------------------------------------
# «Хлебные крошки»
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Crumb:
    """Звено пути к текущей странице."""

    title: Translatable
    url: str = ""

    @property
    def is_link(self) -> bool:
        """Признак того, что звено является ссылкой, а не текущей страницей."""
        return bool(self.url)


def build_breadcrumbs(request: HttpRequest, *crumbs: Crumb) -> list[Crumb]:
    """Собрать путь к текущей странице; первое звено — главная."""
    home = Crumb(title=_("Главная"), url=reverse("core:home"))
    if request.path == home.url:
        return [Crumb(title=_("Главная"))]
    return [home, *crumbs]


def navigation_context(request: HttpRequest) -> dict[str, object]:
    """
    Добавить структуру навигации в контекст шаблона.

    Открытый пункт определяется по началу адреса, поэтому отмечен и на вложенных страницах.
    """
    current_path = request.path

    def is_active(item: NavItem) -> bool:
        url = item.resolve()
        if url == reverse("core:home"):
            return current_path == url
        return current_path.startswith(url)

    def describe(item: NavItem) -> dict[str, object]:
        """Разложить пункт меню по именам, которыми пользуется разметка."""
        children = [
            {
                "title": child.title,
                "url": child.resolve(),
                "hint": child.hint,
                # Иначе в разделе «Анализ» не видно, какой инструмент открыт.
                "active": is_active(child),
            }
            for child in item.children
        ]
        return {
            "title": item.title,
            "url": item.resolve(),
            "icon": item.icon,
            "hint": item.hint,
            # Пункт открыт, если открыт любой из его вложенных или представленных адресов.
            "active": (
                is_active(item)
                or any(child["active"] for child in children)
                or any(is_active(view) for view in item.includes)
            ),
            "children": children,
            "child_active": any(child["active"] for child in children),
            "overview": item.overview or item.title,
        }

    items = [describe(item) for item in MAIN_NAVIGATION]

    footer = [
        {
            "title": group_title,
            "items": [{"title": link.title, "url": link.resolve()} for link in links],
        }
        for group_title, links in FOOTER_NAVIGATION
    ]

    return {"main_navigation": items, "footer_navigation": footer}


def matching_items(query: str) -> list[dict[str, object]]:
    """
    Найти пункты меню, отвечающие запросу, по всем разделам сайта.

    Сравнение идёт по названию и подсказке: «джини» находит «Неравенство».
    """
    words = terms(query)
    if not words:
        return []

    found: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in SITE_SECTIONS:
        # Представленные адреса проверяются раньше пункта: по запросу «карта» найдётся
        # «Карта», а не «Исследовать» с тем же адресом.
        for candidate in (*item.includes, item, *item.children):
            haystack = normalize(f"{candidate.title} {candidate.hint}")
            url = candidate.resolve()
            if url in seen or not all(word in haystack for word in words):
                continue
            seen.add(url)
            found.append(
                {
                    "title": candidate.title,
                    "hint": candidate.hint,
                    "url": url,
                    "icon": candidate.icon or item.icon,
                }
            )
    return found
