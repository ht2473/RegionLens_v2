"""
Страницы учётных записей: ``auth`` — вход и пароль, ``cabinet`` — обзор и профиль,
``settings`` — страница «Настройки», ``security`` и ``data`` — её действия,
``region`` — «Мой регион».
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
    RegisterConfirmView,
    RegisterDoneView,
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
    SignOutOthersView,
    TwoFactorDisableView,
    TwoFactorSetupView,
)
from .settings import SettingsPartView, SettingsView

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
    "RegisterConfirmView",
    "RegisterDoneView",
    "RegisterView",
    "SettingsPartView",
    "SettingsView",
    "SignOutOthersView",
    "TwoFactorDisableView",
    "TwoFactorSetupView",
]
