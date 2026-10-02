"""Адрес ``/rankings/`` рабочей поверхности; сборка рейтинга — в :mod:`apps.rankings.panel`."""

from __future__ import annotations

from apps.surface.panels import PANEL_RANKINGS
from apps.surface.views import SurfaceView


class RankingView(SurfaceView):
    """Рейтинг субъектов по выбранному ряду с движением позиций."""

    panel_code = PANEL_RANKINGS
