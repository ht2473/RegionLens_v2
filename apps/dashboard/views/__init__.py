"""Страницы панели управления, по модулю на раздел."""

from __future__ import annotations

from .content import (
    ContentView,
    GlossaryTermCreateView,
    GlossaryTermDeleteView,
    GlossaryTermEditView,
    MethodologySectionEditView,
)
from .data import (
    CandidateDecisionView,
    DatasetUploadView,
    DatasetVersionEditView,
    DataView,
    EtlRunDetailView,
    QualityView,
)
from .overview import IndexView
from .sources import CollectNowView, ReleaseDetailView, SourcesView
from .tickets import TicketDetailView, TicketListView
from .users import (
    UserDetailView,
    UserListView,
    UserProfileUpdateView,
    UserRoleUpdateView,
    UserToggleActiveView,
    UserTwoFactorResetView,
)
from .visits import VisitsExportView, VisitsView

__all__ = [
    "CandidateDecisionView",
    "CollectNowView",
    "ContentView",
    "DataView",
    "DatasetUploadView",
    "DatasetVersionEditView",
    "EtlRunDetailView",
    "GlossaryTermCreateView",
    "GlossaryTermDeleteView",
    "GlossaryTermEditView",
    "IndexView",
    "MethodologySectionEditView",
    "QualityView",
    "ReleaseDetailView",
    "SourcesView",
    "TicketDetailView",
    "TicketListView",
    "UserDetailView",
    "UserListView",
    "UserProfileUpdateView",
    "UserRoleUpdateView",
    "UserToggleActiveView",
    "UserTwoFactorResetView",
    "VisitsExportView",
    "VisitsView",
]
