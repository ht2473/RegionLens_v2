"""
Страницы учётных записей: ``auth`` — вход и пароль, ``cabinet`` — обзор и профиль,
``security`` — безопасность, ``data`` — свои данные, ``region`` — «Мой регион».
"""

from __future__ import annotations

from .auth import (
    LoginCodeView,
    LoginView,
    LogoutView,
    PasswordChangeView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
    RegisterView,
)
from .cabinet import CabinetOverviewView, ProfileView
from .data import ConsentView, DataView, ExportView
from .region import MyRegionView
from .security import (
    EmailChangeCancelView,
    EmailChangeView,
    EmailConfirmView,
    RecoveryCodesView,
    SecurityView,
    SignOutOthersView,
    TwoFactorDisableView,
    TwoFactorSetupView,
)

__all__ = [
    "CabinetOverviewView",
    "ConsentView",
    "DataView",
    "EmailChangeCancelView",
    "EmailChangeView",
    "EmailConfirmView",
    "ExportView",
    "LoginCodeView",
    "LoginView",
    "LogoutView",
    "MyRegionView",
    "PasswordChangeView",
    "PasswordResetCompleteView",
    "PasswordResetConfirmView",
    "PasswordResetDoneView",
    "PasswordResetView",
    "ProfileView",
    "RecoveryCodesView",
    "RegisterView",
    "SecurityView",
    "SignOutOthersView",
    "TwoFactorDisableView",
    "TwoFactorSetupView",
]
