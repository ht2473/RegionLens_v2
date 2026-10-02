"""Адрес ``/map/`` рабочей поверхности; сборка карты — в :mod:`apps.maps.panel`."""

from __future__ import annotations

from apps.surface.panels import PANEL_MAP
from apps.surface.views import SurfaceView


class ChoroplethView(SurfaceView):
    """Картограмма распределения значений ряда по субъектам."""

    panel_code = PANEL_MAP
