"""Подписи поиска: примеры вопросов под полем и названия видов ответа."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from apps.search import parse

# Примеры вопросов под полем поиска: по одному на частый вид ответа.
EXAMPLES = (
    _("где самые высокие зарплаты"),
    _("безработица в Татарстане"),
    _("как менялась рождаемость"),
)

KIND_LABELS = {
    parse.RANK: _("Где больше и где меньше"),
    parse.VALUE: _("Значение в регионе"),
    parse.COMPARE: _("Сравнение регионов"),
    parse.TREND: _("Как менялся"),
    parse.RELATION: _("Связь показателей"),
    parse.REGION: _("Регион"),
    parse.DEFINE: _("Термин"),
    parse.METHOD: _("Методика"),
}
