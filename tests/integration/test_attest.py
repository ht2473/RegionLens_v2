"""
Подпись выдаваемых документов, страница проверки и подписанная опись архива выпусков.

Ключи и сертификаты выпускаются на каждый прогон во временный каталог: рабочих ключей
проверки не касаются.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import Client
from django.urls import reverse
from django.utils import translation

provenance = pytest.importorskip("provenance")

from provenance import ca, cms, log  # noqa: E402

from apps.attest import archive as sealed  # noqa: E402
from apps.attest import keys, services  # noqa: E402
from apps.attest.models import IssuedDocument  # noqa: E402
from apps.exports import services as export_services  # noqa: E402
from apps.exports.constants import ExportFormat, ReportKind  # noqa: E402
from apps.sources import archive, collect  # noqa: E402
from apps.sources.models import Release  # noqa: E402
from tests.integration.test_exports import sample_document  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

DOCUMENT = "Субъект;2024\nПсковская область;612,3\n".encode()


@pytest.fixture(scope="module")
def root() -> ca.Identity:
    """Корневой центр проверок."""
    return ca.new_root("Проверочный центр RegionLens", "RegionLens")


@pytest.fixture(scope="module")
def signer(root: ca.Identity) -> ca.Identity:
    """Служба подписи, выпущенная корневым центром."""
    return ca.issue_signer(root, "Служба подписи RegionLens", "RegionLens")


@pytest.fixture
def attest_dir(settings: Any, tmp_path: Path, root: ca.Identity, signer: ca.Identity) -> Path:
    """Каталог подписи, как на стенде: ключ службы, её сертификат и корневой сертификат."""
    directory = tmp_path / "attest"
    directory.mkdir()
    ca.save_key(signer.key, directory / keys.SIGNER_KEY)
    ca.save_certificate(signer.certificate, directory / keys.SIGNER_CERTIFICATE)
    ca.save_certificate(root.certificate, directory / keys.ROOT_CERTIFICATE)
    settings.ATTEST_DIR = directory
    return directory


@pytest.fixture
def no_keys(settings: Any, tmp_path: Path) -> None:
    """Каталог подписи пуст: подпись выключена."""
    settings.ATTEST_DIR = tmp_path / "empty"


@pytest.fixture(autouse=True)
def fresh_limits() -> None:
    """Счётчики пределов частоты не переходят из проверки в проверку."""
    cache.clear()


def revoke(directory: Path, root: ca.Identity, signer: ca.Identity) -> None:
    """Положить в каталог список отзыва с сертификатом службы."""
    moment = datetime.now(UTC) - timedelta(minutes=1)
    crl = ca.make_crl(root, [(signer.certificate.serial_number, moment)])
    ca.save_crl(crl, directory / keys.CRL)


class TestIssue:
    """Выдача документа: подпись и запись в реестр выданных."""

    def test_issued_document_is_found_by_the_file_alone(self, attest_dir: Path) -> None:
        """Документ, выданный сайтом, узнаётся по одному файлу, без подписи рядом."""
        record = services.issue(DOCUMENT, kind="series", export_format="csv", filename="a.csv")

        outcome = services.check(DOCUMENT)

        assert record is not None
        assert outcome.status == services.Status.AUTHENTIC
        assert outcome.record == record
        assert outcome.verified is not None
        assert outcome.verified.signer == "Служба подписи RegionLens"

    def test_same_document_is_recorded_once(self, attest_dir: Path) -> None:
        """Тот же документ второй раз не подписывается: запись находится по хэшу."""
        first = services.issue(DOCUMENT, kind="series", export_format="csv", filename="a.csv")
        second = services.issue(DOCUMENT, kind="series", export_format="csv", filename="b.csv")

        assert first == second
        assert IssuedDocument.objects.count() == 1

    def test_without_keys_nothing_is_signed(self, no_keys: None) -> None:
        """Без ключа документы выдаются как прежде, а проверке нечем проверять."""
        assert services.issue(DOCUMENT, kind="series", export_format="csv", filename="a") is None
        assert services.check(DOCUMENT).status == services.Status.UNAVAILABLE
        assert IssuedDocument.objects.count() == 0


class TestCheck:
    """Проверка документа: по реестру и по приложенной подписи."""

    def test_changed_digit_is_not_found(self, attest_dir: Path) -> None:
        """Изменённое число делает документ незнакомым сайту."""
        services.issue(DOCUMENT, kind="series", export_format="csv", filename="a.csv")
        forged = DOCUMENT.replace(b"612", b"712")

        assert services.check(forged).status == services.Status.UNKNOWN

    def test_changed_document_is_refused_by_its_signature(self, attest_dir: Path) -> None:
        """С приложенной подписью изменённый документ отклоняется и названа причина."""
        record = services.issue(DOCUMENT, kind="series", export_format="csv", filename="a.csv")
        assert record is not None
        forged = DOCUMENT.replace(b"612", b"712")

        outcome = services.check(forged, bytes(record.signature))

        assert outcome.status == services.Status.REFUSED
        assert outcome.code == "document_changed"

    def test_signature_of_another_center_is_refused(self, attest_dir: Path) -> None:
        """Подпись чужого центра с теми же именами не проходит: корень другой."""
        stranger = ca.issue_signer(
            ca.new_root("Проверочный центр RegionLens", "RegionLens"), "Служба подписи RegionLens"
        )

        outcome = services.check(DOCUMENT, cms.sign(DOCUMENT, stranger))

        assert outcome.status == services.Status.REFUSED
        assert outcome.code == "untrusted"

    def test_registry_row_without_real_signature_proves_nothing(self, attest_dir: Path) -> None:
        """Строка реестра, вписанная в базу с чужой подписью, проверку не проходит."""
        stranger = ca.issue_signer(ca.new_root("Другой центр"), "Служба подписи")
        IssuedDocument.objects.create(
            sha256=archive.sha256_of(DOCUMENT),
            size=len(DOCUMENT),
            kind="series",
            export_format="csv",
            filename="a.csv",
            signature=cms.sign(DOCUMENT, stranger),
        )

        assert services.check(DOCUMENT).status == services.Status.REFUSED

    def test_revoked_signer_is_refused(
        self, attest_dir: Path, root: ca.Identity, signer: ca.Identity
    ) -> None:
        """После отзыва сертификата службы её подписи не принимаются, и прежние тоже."""
        services.issue(DOCUMENT, kind="series", export_format="csv", filename="a.csv")
        revoke(attest_dir, root, signer)

        outcome = services.check(DOCUMENT)

        assert outcome.status == services.Status.REFUSED
        assert outcome.code == "revoked"

    def test_old_records_are_pruned(self, attest_dir: Path, settings: Any) -> None:
        """Записи старше срока хранения убираются; свежие остаются."""
        record = services.issue(DOCUMENT, kind="series", export_format="csv", filename="a.csv")
        assert record is not None
        old = datetime.now(UTC) - timedelta(days=settings.ATTEST_REGISTRY_DAYS + 1)
        IssuedDocument.objects.filter(pk=record.pk).update(created_at=old)
        services.issue(b"other", kind="series", export_format="csv", filename="b.csv")

        assert services.prune() == 1
        assert IssuedDocument.objects.count() == 1


class TestExportSigning:
    """Выгрузка документа подписывает только данные склада."""

    @pytest.fixture(autouse=True)
    def stub_builder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Отчёт без обращения к складу."""
        monkeypatch.setattr(
            export_services, "build_report", lambda *args, **kwargs: sample_document()
        )

    def _download(self, client: Client, series: str) -> Any:
        return client.get(
            reverse("exports:document"),
            {"kind": ReportKind.RANKING, "series": series, "format": ExportFormat.CSV},
        )

    def test_downloaded_document_is_in_the_registry(self, client: Client, attest_dir: Path) -> None:
        """Скачанный документ находится в реестре по своему хэшу."""
        response = self._download(client, "E000000001:00")

        assert response.status_code == 200
        assert services.check(response.content).status == services.Status.AUTHENTIC

    def test_user_table_is_not_signed(
        self, client: Client, attest_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """За числа таблицы пользователя сайт не ручается: такой документ не подписан."""
        monkeypatch.setattr("apps.exports.views.exportable", lambda key: True)

        response = self._download(client, "u:abc123:s1")

        assert response.status_code == 200
        assert IssuedDocument.objects.count() == 0

    def test_registry_failure_does_not_block_download(
        self, client: Client, attest_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Сбой записи в реестр не мешает выдаче: документ отдаётся без подписи."""

        def broken(*args: Any, **kwargs: Any) -> None:
            raise DatabaseError("реестр недоступен")

        monkeypatch.setattr(services, "issue", broken)

        assert self._download(client, "E000000001:00").status_code == 200


class TestVerifyPage:
    """Страница «Проверить документ» и файлы для проверки без сайта."""

    def _post(self, client: Client, content: bytes, signature: bytes | None = None) -> Any:
        data: dict[str, Any] = {"document": SimpleUploadedFile("report.csv", content)}
        if signature is not None:
            data["signature"] = SimpleUploadedFile("report.csv.p7s", signature)
        return client.post(reverse("attest:verify"), data)

    def test_page_publishes_the_root_fingerprint(
        self, client: Client, attest_dir: Path, root: ca.Identity
    ) -> None:
        """Страница называет отпечаток корневого сертификата: с ним сверяют проверку без сайта."""
        response = client.get(reverse("attest:verify"))

        assert response.status_code == 200
        assert ca.fingerprint(root.certificate) in response.content.decode()

    def test_issued_document_is_authentic(self, client: Client, attest_dir: Path) -> None:
        """Выданный документ признаётся подлинным."""
        services.issue(DOCUMENT, kind="series", export_format="csv", filename="report.csv")

        response = self._post(client, DOCUMENT)

        assert "Документ подлинный" in response.content.decode()

    def test_changed_document_is_not_recognised(self, client: Client, attest_dir: Path) -> None:
        """Изменённый документ сайт не узнаёт."""
        services.issue(DOCUMENT, kind="series", export_format="csv", filename="report.csv")

        response = self._post(client, DOCUMENT.replace(b"612", b"712"))

        assert "Такого документа сайт не выдавал" in response.content.decode()

    def test_changed_document_with_signature_names_the_reason(
        self, client: Client, attest_dir: Path
    ) -> None:
        """С файлом подписи причина отказа названа словами."""
        record = services.issue(DOCUMENT, kind="series", export_format="csv", filename="r.csv")
        assert record is not None

        response = self._post(client, DOCUMENT.replace(b"612", b"712"), bytes(record.signature))

        page = response.content.decode()
        assert "Подлинность не подтверждена" in page
        assert "Документ изменён после подписи" in page

    def test_visitor_may_send_document_larger_than_form_limit(
        self, client: Client, attest_dir: Path, settings: Any
    ) -> None:
        """Без входа страница принимает документ больше общего предела тела — до своего."""
        large = DOCUMENT + b"0" * (2 * 1024 * 1024)
        assert len(large) > settings.ANONYMOUS_BODY_BYTES

        for language in ("ru", "en"):
            with translation.override(language):
                url = reverse("attest:verify")
            response = client.post(url, {"document": SimpleUploadedFile("report.csv", large)})
            assert response.status_code == 200, language

        login = client.post(
            reverse("accounts:login"), {"document": SimpleUploadedFile("report.csv", large)}
        )
        assert login.status_code == 413

    def test_document_over_page_limit_is_refused_unread(
        self, client: Client, attest_dir: Path, settings: Any
    ) -> None:
        """Тело больше предела страницы отклоняется до чтения."""
        settings.ANONYMOUS_BODY_ROUTES = {"attest:verify": 2 * 1024 * 1024}

        response = self._post(client, b"0" * (3 * 1024 * 1024))

        assert response.status_code == 413

    def test_page_without_keys_says_signing_is_off(self, client: Client, no_keys: None) -> None:
        """Без ключей страница честно говорит, что проверять нечем."""
        response = self._post(client, DOCUMENT)

        assert "Подпись документов не настроена" in response.content.decode()

    def test_signature_is_served_by_document_hash(self, client: Client, attest_dir: Path) -> None:
        """Подпись выданного документа скачивается по его хэшу."""
        record = services.issue(DOCUMENT, kind="series", export_format="csv", filename="r.csv")
        assert record is not None

        response = client.get(reverse("attest:signature", kwargs={"sha256": record.sha256}))
        unknown = client.get(reverse("attest:signature", kwargs={"sha256": "0" * 64}))

        assert response.content == bytes(record.signature)
        assert unknown.status_code == 404

    def test_only_public_files_are_served(self, client: Client, attest_dir: Path) -> None:
        """Корневой сертификат отдаётся, ключ службы — нет."""
        root_file = client.get(reverse("attest:trust-file", kwargs={"name": "root.crt"}))
        key_file = client.get("/ru/verify/trust/signer.key")

        assert root_file.status_code == 200
        assert b"BEGIN CERTIFICATE" in root_file.content
        assert key_file.status_code == 404


class TestArchiveSeal:
    """Опись архива выпусков с подписанной вершиной."""

    @pytest.fixture
    def archive_dir(self, settings: Any, tmp_path: Path) -> Path:
        settings.SOURCE_ARCHIVE_DIR = tmp_path / "archive"
        return tmp_path / "archive"

    def _store(self, name: str, content: bytes) -> archive.ArchivedFile:
        record, _is_new = archive.store("cbr", name, content)
        return record

    def test_new_file_is_sealed_at_once(self, attest_dir: Path, archive_dir: Path) -> None:
        """Каждая новая запись архива сразу покрыта подписанной вершиной."""
        self._store("a.xlsx", b"first")
        self._store("b.xlsx", b"second")

        assert archive.verify() == []

    def test_file_replaced_with_its_manifest_line_is_caught(
        self, attest_dir: Path, archive_dir: Path
    ) -> None:
        """Подмена файла вместе со строкой описи видна: вершина с описью не сходится."""
        record = self._store("a.xlsx", b"first")
        manifest = archive_dir / archive.MANIFEST_NAME
        forged = b"forged"
        path = record.absolute_path
        path.chmod(0o644)
        path.write_bytes(forged)
        text = manifest.read_bytes().replace(
            record.sha256.encode(), archive.sha256_of(forged).encode()
        )
        manifest.write_bytes(text.replace(b",5,", b",6,"))

        assert archive.verify_files() == []
        assert sealed.problems()

    def test_deleted_head_is_caught(self, attest_dir: Path, archive_dir: Path) -> None:
        """Удаление подписанной вершины не снимает защиту: это тоже подмена."""
        self._store("a.xlsx", b"first")
        head, _signature, _history = log.paths(archive_dir / archive.MANIFEST_NAME)
        head.unlink()

        assert sealed.problems()

    def test_unsealed_line_is_a_warning(self, attest_dir: Path, archive_dir: Path) -> None:
        """Запись, дописанная без подписи, — предупреждение, а не подмена."""
        self._store("a.xlsx", b"first")
        manifest = archive_dir / archive.MANIFEST_NAME
        line = manifest.read_bytes().splitlines()[-1].replace(b"a.xlsx", b"c.xlsx")
        manifest.write_bytes(manifest.read_bytes() + line + b"\n")

        assert sealed.problems() == []
        assert sealed.unsealed()

    def test_tampered_archive_stops_rebuild(
        self, attest_dir: Path, archive_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """По подменённому архиву склад не пересобирается: действует прежний."""
        self._store("a.xlsx", b"first")
        head, _signature, _history = log.paths(archive_dir / archive.MANIFEST_NAME)
        head.unlink()
        executed: list[Any] = []
        monkeypatch.setattr("apps.warehouse.builds.execute", lambda *a, **k: executed.append(a))
        messages: list[str] = []

        assert collect.rebuild_warehouse(messages.append) is False
        assert executed == []
        assert any("Склад не пересобран" in message for message in messages)

    def test_seal_command_refuses_mismatched_files(
        self, attest_dir: Path, archive_dir: Path
    ) -> None:
        """Опись, не сошедшуюся с файлами, команда не подписывает."""
        record = self._store("a.xlsx", b"first")
        record.absolute_path.chmod(0o644)
        record.absolute_path.write_bytes(b"forged")

        with pytest.raises(CommandError, match="не сходится"):
            call_command("archive_seal")

    def test_seal_command_reseals_after_new_signer(
        self, attest_dir: Path, archive_dir: Path, root: ca.Identity, signer: ca.Identity
    ) -> None:
        """После отзыва ключа опись подписывается заново новым, и проверка снова сходится."""
        self._store("a.xlsx", b"first")
        revoke(attest_dir, root, signer)
        assert sealed.problems()
        fresh = ca.issue_signer(root, "Служба подписи RegionLens", "RegionLens")
        ca.save_key(fresh.key, attest_dir / keys.SIGNER_KEY)
        ca.save_certificate(fresh.certificate, attest_dir / keys.SIGNER_CERTIFICATE)

        call_command("archive_seal")

        assert sealed.problems() == []

    def test_replaced_archive_file_is_not_parsed(
        self, attest_dir: Path, archive_dir: Path, settings: Any, tmp_path: Path
    ) -> None:
        """Подменённый в архиве файл не разбирается: хэш не тот, что при получении."""
        settings.SOURCE_PARSED_DIR = tmp_path / "parsed"
        record = self._store("a.xlsx", b"first")
        release = Release.objects.create(
            source=collect.sync_source("cbr"),
            code="2026-11-01",
            title="проверка",
            reference_year=2026,
            file_name="a.xlsx",
            archive_path=record.path,
            sha256=record.sha256,
            size_bytes=record.size,
            fetched_at=datetime.now(UTC),
        )
        record.absolute_path.chmod(0o644)
        record.absolute_path.write_bytes(b"forged")

        assert collect.parse_release(release, report=lambda message: None) is False
        release.refresh_from_db()
        assert "изменён после получения" in release.error
