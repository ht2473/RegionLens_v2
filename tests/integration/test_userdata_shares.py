"""
Закрытые ссылки на таблицу: создание и показ один раз, чтение без входа, только чтение,
скачивание по разрешению, отзыв, срок, язык читателя и предел подбора токена.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.userdata import shares
from apps.userdata.models import Dataset, Share
from tests.support.userdata import built as _built
from tests.support.userdata import first_record as _first
from tests.support.userdata import key_of as _key
from tests.support.userdata import userdata_dir  # noqa: F401 — приспособление модуля

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

VIEWS = (
    "maps:choropleth",
    "compare:index",
    "rankings:index",
    "surface:distribution",
    "surface:table",
)
LINK = re.compile(r'value="(http://testserver/s/[A-Za-z0-9_-]+)"')


def _share(client: Client, dataset: Dataset, *, downloads: bool = False, days: int = 30) -> str:
    """Создать ссылку от имени владельца и вернуть её адрес со страницы таблицы."""
    data: dict[str, Any] = {"days": str(days)}
    if downloads:
        data["downloads"] = "on"
    response = client.post(reverse("userdata:share-create", args=[dataset.public_id]), data)
    assert response.status_code == 302
    page = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
    found = LINK.search(page.text)
    assert found, "ссылка не показана владельцу"
    return found.group(1).removeprefix("http://testserver")


class TestCreate:
    """Владелец создаёт ссылку; адрес показывается один раз, в базе — отпечаток."""

    def test_shown_once_and_stored_as_hash(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        path = _share(member_client, dataset)
        token = path.removeprefix("/s/")
        share = Share.objects.get(dataset=dataset)
        assert share.token_hash == shares.fingerprint(token)
        assert token not in share.token_hash
        again = member_client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        assert not LINK.search(again.text)
        assert "видно вам и по закрытой ссылке" in again.text
        assert (share.expires_at - timezone.now()).days in {29, 30}

    def test_guest_cannot_share(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client)
        client.post(reverse("userdata:share-create", args=[dataset.public_id]), {"days": "30"})
        assert not Share.objects.exists()
        page = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        assert "Войдите, чтобы создать ссылку" in page.text

    def test_stranger_cannot_share(
        self, member_client: Client, make_user: Any, warehouse: Any
    ) -> None:
        dataset = _built(member_client)
        other = Client()
        other.force_login(make_user(email="other@example.com"))
        response = other.post(
            reverse("userdata:share-create", args=[dataset.public_id]), {"days": "30"}
        )
        assert response.status_code == 404
        assert not Share.objects.exists()

    def test_limit(self, member_client: Client, settings: Any, warehouse: Any) -> None:
        settings.USERDATA_MAX_SHARES = 2
        dataset = _built(member_client)
        for _ in range(3):
            member_client.post(
                reverse("userdata:share-create", args=[dataset.public_id]), {"days": "7"}
            )
        assert Share.objects.filter(dataset=dataset).count() == 2


class TestReader:
    """Читатель ссылки видит таблицу и её виды без входа и ничего не меняет."""

    def test_reads_without_login(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        path = _share(member_client, dataset)
        reader = Client()
        opened = reader.get(path)
        assert opened.status_code == 302
        assert opened["Location"] == reverse("userdata:dataset", args=[dataset.public_id])
        assert opened["Cache-Control"] == "private, no-store"
        page = reader.get(opened["Location"])
        assert page.status_code == 200
        text = page.text
        assert "открыто по закрытой ссылке" in text
        assert 'name="robots" content="noindex' in text
        # Только чтение: ни описания, ни формул, ни ссылок, ни удаления, ни файла.
        for url in (
            reverse("userdata:delete", args=[dataset.public_id]),
            reverse("userdata:series", args=[dataset.public_id]),
            reverse("userdata:formula-new", args=[dataset.public_id]),
            reverse("userdata:share-create", args=[dataset.public_id]),
        ):
            assert url not in text, url
        key = _key(dataset, _first(dataset))
        for name in VIEWS:
            response = reader.get(reverse(name), {"series": key})
            assert response.status_code == 200, name
            assert "открыто по закрытой ссылке" in response.text, name

    def test_cannot_change(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        path = _share(member_client, dataset)
        reader = Client()
        reader.get(path)
        for name in ("userdata:file", "userdata:table", "userdata:series", "userdata:formula-new"):
            assert reader.get(reverse(name, args=[dataset.public_id])).status_code == 404, name
        response = reader.post(
            reverse("userdata:delete", args=[dataset.public_id]), {"confirm": "1"}
        )
        assert response.status_code == 404
        assert Dataset.objects.filter(pk=dataset.pk).exists()
        assert (
            reader.post(
                reverse("userdata:share-create", args=[dataset.public_id]), {"days": "30"}
            ).status_code
            == 404
        )

    def test_only_this_table(self, member_client: Client, warehouse: Any) -> None:
        shared = _built(member_client)
        other = _built(member_client)
        path = _share(member_client, shared)
        reader = Client()
        reader.get(path)
        assert reader.get(reverse("userdata:dataset", args=[other.public_id])).status_code == 404
        key = _key(other, _first(other))
        assert reader.get(reverse("maps:choropleth"), {"series": key}).status_code == 404

    def test_no_downloads_by_default(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        path = _share(member_client, dataset)
        reader = Client()
        reader.get(path)
        for kind in ("csv", "xlsx", "source"):
            url = reverse("userdata:download", args=[dataset.public_id, kind])
            assert reader.get(url).status_code == 404, kind
        key = _key(dataset, _first(dataset))
        document = reader.get(
            reverse("exports:document"), {"kind": "series", "series": key, "format": "csv"}
        )
        assert document.status_code == 404
        page = reader.get(reverse("userdata:dataset", args=[dataset.public_id]))
        assert reverse("userdata:download", args=[dataset.public_id, "csv"]) not in page.text

    def test_downloads_when_allowed(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        path = _share(member_client, dataset, downloads=True)
        reader = Client()
        reader.get(path)
        csv_url = reverse("userdata:download", args=[dataset.public_id, "csv"])
        assert reader.get(csv_url).status_code == 200
        source = reverse("userdata:download", args=[dataset.public_id, "source"])
        assert reader.get(source).status_code == 404
        key = _key(dataset, _first(dataset))
        document = reader.get(
            reverse("exports:document"), {"kind": "series", "series": key, "format": "csv"}
        )
        assert document.status_code == 200

    def test_counts_opening(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        path = _share(member_client, dataset)
        Client().get(path)
        Client().get(path)
        share = Share.objects.get(dataset=dataset)
        assert share.opened_count == 2
        assert share.last_opened_at is not None


class TestEnd:
    """Отозванная и истёкшая ссылка ничего не открывает — и у тех, кто открыл её раньше."""

    def test_revoke(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        path = _share(member_client, dataset)
        reader = Client()
        reader.get(path)
        share = Share.objects.get(dataset=dataset)
        member_client.post(
            reverse("userdata:share-revoke", args=[dataset.public_id, share.public_id])
        )
        share.refresh_from_db()
        assert share.revoked_at is not None
        page = reader.get(reverse("userdata:dataset", args=[dataset.public_id]))
        assert page.status_code == 404
        key = _key(dataset, _first(dataset))
        assert reader.get(reverse("maps:choropleth"), {"series": key}).status_code == 404
        gone = Client().get(path)
        assert gone.status_code == 404
        assert "Ссылка больше не действует" in gone.text

    def test_stranger_cannot_revoke(
        self, member_client: Client, make_user: Any, warehouse: Any
    ) -> None:
        dataset = _built(member_client)
        _share(member_client, dataset)
        share = Share.objects.get(dataset=dataset)
        other = Client()
        other.force_login(make_user(email="other@example.com"))
        response = other.post(
            reverse("userdata:share-revoke", args=[dataset.public_id, share.public_id])
        )
        assert response.status_code == 404
        share.refresh_from_db()
        assert share.revoked_at is None

    def test_expired(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        path = _share(member_client, dataset)
        Share.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        assert Client().get(path).status_code == 404

    def test_deleted_table(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        path = _share(member_client, dataset)
        member_client.post(reverse("userdata:delete", args=[dataset.public_id]), {"confirm": "1"})
        assert not Share.objects.exists()
        assert Client().get(path).status_code == 404


class TestLink:
    """Адрес ссылки: язык читателя, подбор токена, поисковики."""

    def test_reader_language(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        path = _share(member_client, dataset)
        reader = Client(HTTP_ACCEPT_LANGUAGE="en")
        opened = reader.get(path)
        assert opened["Location"].startswith("/en/own-data/")
        assert reader.get(opened["Location"]).status_code == 200

    def test_guessing_is_limited(self, client: Client, settings: Any) -> None:
        settings.USERDATA_SHARE_ATTEMPTS = 3
        statuses = [client.get(f"/s/{'x' * 20}{number}").status_code for number in range(5)]
        assert statuses[:3] == [404, 404, 404]
        assert statuses[-1] == 429

    def test_robots(self, client: Client) -> None:
        assert "Disallow: /s/" in client.get("/robots.txt").text
