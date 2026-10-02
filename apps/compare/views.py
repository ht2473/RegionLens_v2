"""Адрес ``/compare/`` рабочей поверхности; сборка динамики — в :mod:`apps.compare.panel`."""

from __future__ import annotations

from apps.surface.panels import PANEL_COMPARE
from apps.surface.views import SurfaceView


class CompareView(SurfaceView):
    """Сопоставление нескольких территорий по набору показателей."""

    panel_code = PANEL_COMPARE
