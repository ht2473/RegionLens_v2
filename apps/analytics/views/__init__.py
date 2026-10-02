"""Страницы аналитического раздела: по модулю на инструмент, общее — в ``base``."""

from __future__ import annotations

from .base import AnalyticsView
from .convergence import ConvergenceView
from .correlation import CorrelationView
from .index_builder import IndexBuilderView
from .inequality import InequalityView
from .overview import AnalyticsIndexView
from .revisions import RevisionsView, RevisionTraceView
from .spatial import SpatialView

__all__ = [
    "AnalyticsIndexView",
    "AnalyticsView",
    "ConvergenceView",
    "CorrelationView",
    "IndexBuilderView",
    "InequalityView",
    "RevisionTraceView",
    "RevisionsView",
    "SpatialView",
]
