"""Маршруты входа, регистрации, пароля, «Моего региона» и разделов кабинета под ``/cabinet/``."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # --- Вход и регистрация -----------------------------------------------------------
    path("login/", views.LoginView.as_view(), name="login"),
    path("login/code/", views.LoginCodeView.as_view(), name="login-code"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("register/", views.RegisterView.as_view(), name="register"),
    path("my-region/", views.MyRegionView.as_view(), name="my-region"),
    # --- Восстановление пароля ---------------------------------------------------------
    path("password/reset/", views.PasswordResetView.as_view(), name="password-reset"),
    path(
        "password/reset/sent/",
        views.PasswordResetDoneView.as_view(),
        name="password-reset-done",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path(
        "password/reset/done/",
        views.PasswordResetCompleteView.as_view(),
        name="password-reset-complete",
    ),
    # --- Личный кабинет ----------------------------------------------------------------
    path("cabinet/", views.CabinetOverviewView.as_view(), name="dashboard"),
    path("cabinet/settings/", views.SettingsView.as_view(), name="settings"),
    # Прежние адреса разделов: открытие ведёт в «Настройки», формы отправляются сюда.
    path("cabinet/profile/", views.ProfileView.as_view(), name="profile"),
    path(
        "cabinet/security/",
        views.SettingsPartView.as_view(part="security"),
        name="security",
    ),
    path(
        "cabinet/security/password/",
        views.PasswordChangeView.as_view(),
        name="password-change",
    ),
    path("cabinet/security/email/", views.EmailChangeView.as_view(), name="email-change"),
    path(
        "cabinet/security/email/cancel/",
        views.EmailChangeCancelView.as_view(),
        name="email-cancel",
    ),
    path(
        "cabinet/security/email/confirm/<str:token>/",
        views.EmailConfirmView.as_view(),
        name="email-confirm",
    ),
    path("cabinet/security/sessions/", views.SignOutOthersView.as_view(), name="sign-out-others"),
    path("cabinet/security/code/", views.TwoFactorSetupView.as_view(), name="two-factor"),
    path(
        "cabinet/security/code/disable/",
        views.TwoFactorDisableView.as_view(),
        name="two-factor-disable",
    ),
    path(
        "cabinet/security/code/recovery/",
        views.RecoveryCodesView.as_view(),
        name="recovery-codes",
    ),
    path("cabinet/data/", views.DataView.as_view(), name="data"),
    path("cabinet/data/export/", views.ExportView.as_view(), name="data-export"),
    path("cabinet/data/consent/", views.ConsentView.as_view(), name="consent"),
]
