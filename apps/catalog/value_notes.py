"""Пометка наблюдения словами: почему значения нет или что за значение с признаком источника."""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

from .constants import ValueFlag, ValueQuality

# Пояснения к признаку качества — только для необычных наблюдений.
QUALITY_LABELS: dict[int, Any] = {
    ValueQuality.NO_DATA: _("нет данных в источнике"),
    ValueQuality.HIDDEN: _("значение скрыто"),
    ValueQuality.NOT_APPLICABLE: _("неприменимо"),
    ValueQuality.ABSENT: _("явление отсутствует"),
}
# Пометки значений: ноль из прочерка и «0,0» своих таблиц, поправка к исходному набору.
FLAG_LABELS: dict[ValueFlag, Any] = {
    ValueFlag.ABSENT: _("явление отсутствует"),
    ValueFlag.BELOW_UNIT: _("меньше единицы измерения"),
    ValueFlag.CORRECTED: _("ошибка набора исправлена по сборнику Росстата"),
}


def note(value: float | None, quality: int | None, flags: int | None = 0) -> str:
    """Пометка наблюдения: причина пропуска или признак значения; обычное — пусто."""
    if value is None:
        if quality is None:
            return ""
        return str(QUALITY_LABELS.get(quality, _("значение неизвестно")))
    found = ValueFlag(int(flags or 0))
    return "; ".join(str(label) for flag, label in FLAG_LABELS.items() if flag in found)
