"""Маршруты проверки документов."""

from __future__ import annotations

from django.urls import path, re_path

from . import views

app_name = "attest"

urlpatterns = [
    path("verify/", views.VerifyView.as_view(), name="verify"),
    re_path(
        r"^verify/(?P<sha256>[0-9a-fA-F]{64})\.p7s$",
        views.SignatureView.as_view(),
        name="signature",
    ),
    re_path(
        r"^verify/trust/(?P<name>root\.crt|signer\.crt|crl\.pem)$",
        views.TrustFileView.as_view(),
        name="trust-file",
    ),
]
