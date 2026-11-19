"""
Учебный удостоверяющий центр для проверки подписи на своей машине.

Создаёт в ``ATTEST_DIR/ca`` корневой ключ, корневой сертификат и пустой список отзыва,
выпускает им сертификат службы подписи и кладёт в ``ATTEST_DIR`` то, что читает сайт:
ключ и сертификат службы, копии корневого сертификата и списка отзыва. Делает то же,
что команды ``provenance ca init`` и ``provenance ca issue``, поэтому отзывать
сертификат дальше можно самой командой ``provenance``.

Только в разработке (``DEBUG``): на стенде корневой ключ на сервере не хранится,
сертификат службы выпускают в стороне (docs/ARCHITECTURE.md, раздел 4.4.1).
Если ключ службы уже лежит в каталоге, команда ничего не меняет.
"""

from __future__ import annotations

import shutil
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from provenance import cli

from apps.attest import keys

ROOT_NAME = "Учебный корневой центр RegionLens"
SIGNER_NAME = "Служба подписи RegionLens"
ORGANIZATION = "RegionLens, учебный центр"


class Command(BaseCommand):
    """Создать учебный центр и ключ службы подписи, если их ещё нет."""

    help = "Создаёт учебный удостоверяющий центр и ключ службы подписи в ATTEST_DIR (только DEBUG)"

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Создать центр, выпустить сертификат службы и разложить файлы для сайта."""
        directory = keys.directory()
        if (directory / keys.SIGNER_KEY).exists():
            self.stdout.write(f"Ключ службы подписи уже есть ({directory}), центр не создаётся.")
            return
        if not settings.DEBUG:
            self.stdout.write(
                self.style.WARNING(
                    "Учебный центр создаётся только в разработке (DEBUG). На стенде сертификат "
                    "службы выпускают вне сервера: docs/ARCHITECTURE.md, раздел 4.4.1."
                )
            )
            return

        authority = directory / "ca"
        steps = [
            ["ca", "issue", str(authority), "--name", SIGNER_NAME, "--org", ORGANIZATION,
             "--out", str(directory)],
        ]  # fmt: skip
        # Центр мог остаться от прежнего запуска без ключа службы: второй раз его не создать.
        if not (authority / cli.ROOT_KEY).exists():
            steps.insert(
                0, ["ca", "init", str(authority), "--name", ROOT_NAME, "--org", ORGANIZATION]
            )
        for step in steps:
            if cli.main(step) != 0:
                raise CommandError(f"Учебный центр не создан: provenance {' '.join(step[:2])}")
        for name in (keys.ROOT_CERTIFICATE, keys.CRL):
            shutil.copyfile(authority / name, directory / name)

        self.stdout.write(
            self.style.SUCCESS(
                f"Учебный центр создан. Сайт подписывает документы ключом из {directory}; "
                f"корневой ключ — в {authority}. Только для проверки на этой машине."
            )
        )
