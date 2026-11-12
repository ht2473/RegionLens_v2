"""
Что может посетитель без входа: «Свои данные» ничего не принимают и не заводят, большое
тело запроса отклоняется до чтения.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.userdata import access, groups, studies
from apps.userdata.models import Dataset, RegionGroup, Study

pytestmark = [pytest.mark.integration, pytest.mark.security, pytest.mark.django_db]

TABLE = "Субъект;2024\nПсковская область;1,5\nТверская область;2,5\n".encode()


class TestOwnDataWithoutSignIn:
    """Раздел «Свои данные» гостю только рассказывает о себе."""

    def test_section_invites_to_sign_in(self, client: Client) -> None:
        page = client.get(reverse("userdata:index"))
        text = page.content.decode()

        assert page.status_code == 200
        assert "Свои таблицы — после входа" in text
        assert reverse("accounts:login") in text
        assert reverse("accounts:register") in text
        assert "data-upload-form" not in text
        assert reverse("userdata:example") not in text

    @pytest.mark.parametrize(
        ("name", "method", "data"),
        [
            ("userdata:upload", "get", {}),
            ("userdata:upload", "post", {"action": "paste", "text": "Субъект\t2024\nМосква\t1"}),
            ("userdata:example", "post", {}),
            ("userdata:study-new", "post", {"title": "Исследование"}),
            ("userdata:study-add", "post", {"study": "new", "target": "maps:choropleth"}),
            ("userdata:groups", "get", {}),
            ("userdata:groups", "post", {"title": "Группа", "codes": ["RU-MOW"]}),
        ],
    )
    def test_entry_pages_send_guest_to_sign_in(
        self, client: Client, name: str, method: str, data: dict[str, Any]
    ) -> None:
        response = getattr(client, method)(reverse(name), data)

        assert response.status_code == 302
        assert response["Location"].startswith(reverse("accounts:login"))

    def test_upload_without_sign_in_leaves_nothing(self, client: Client, tmp_path: Any) -> None:
        """Ни таблицы, ни исследования, ни сеанса: запрос без cookie не оставляет следа в базе."""
        client.post(
            reverse("userdata:upload"),
            {"action": "upload", "file": SimpleUploadedFile("table.csv", TABLE)},
        )
        for _ in range(3):
            Client().post(reverse("userdata:study-new"), {"title": "Исследование"})

        assert not Dataset.objects.exists()
        assert not Study.objects.exists()
        assert not RegionGroup.objects.exists()
        assert not Session.objects.exists()

    def test_nothing_is_owned_or_created_without_account(self) -> None:
        request = RequestFactory().post("/")
        request.user = AnonymousUser()

        assert not access.owned(request).exists()
        assert not studies.owned(request).exists()
        assert not groups.owned(request).exists()
        with pytest.raises(PermissionDenied):
            access.assign_owner(Dataset(title="x"), request)
        with pytest.raises(PermissionDenied):
            studies.create(request, "Исследование")

    def test_save_menu_offers_sign_in_only(self, client: Client, warehouse: Any) -> None:
        """«В исследование» гостю не предлагается: в меню — только вход."""
        text = client.get(reverse("maps:choropleth")).content.decode()

        assert "Войти, чтобы сохранить" in text
        assert reverse("userdata:study-add") not in text


class TestBodyLimit:
    """Большое тело запроса отклоняется до чтения тому, кому оно не положено."""

    BIG = {"text": "x" * 4096}

    def test_guest_cannot_send_a_large_body(self, client: Client, settings: Any) -> None:
        settings.ANONYMOUS_BODY_BYTES = 1024

        response = client.post(reverse("feedback:create"), self.BIG)

        assert response.status_code == 413
        assert response["Content-Type"].startswith("text/plain")

    def test_guest_forms_pass(self, client: Client, settings: Any) -> None:
        settings.ANONYMOUS_BODY_BYTES = 1024

        response = client.post(reverse("accounts:login"), {"username": "a@b.ru", "password": "x"})

        assert response.status_code == 200

    def test_member_sends_a_table(self, member_client: Client, settings: Any) -> None:
        settings.ANONYMOUS_BODY_BYTES = 1024

        response = member_client.post(reverse("feedback:create"), self.BIG)

        assert response.status_code != 413

    def test_only_data_manager_sends_more_than_a_table(
        self, member_client: Client, settings: Any
    ) -> None:
        settings.ANONYMOUS_BODY_BYTES = 1024
        settings.MEMBER_BODY_BYTES = 2048

        response = member_client.post(reverse("dashboard:data-upload"), self.BIG)

        assert response.status_code == 413

    def test_data_manager_sends_a_dataset(self, admin_panel_client: Client, settings: Any) -> None:
        settings.ANONYMOUS_BODY_BYTES = 1024
        settings.MEMBER_BODY_BYTES = 2048

        response = admin_panel_client.post(reverse("dashboard:data-upload"), self.BIG)

        assert response.status_code != 413

    def test_body_without_declared_length_is_refused(self, client: Client) -> None:
        """Тело без объявленной длины сервер читал бы, не зная, сколько ждать."""
        response = client.post(
            reverse("accounts:login"),
            {"username": "a@b.ru", "password": "x"},
            CONTENT_LENGTH="",
            HTTP_TRANSFER_ENCODING="chunked",
        )

        assert response.status_code == 411
