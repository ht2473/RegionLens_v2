"""Подписать вершину описи архива выпусков: первый раз и после смены ключа подписи."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from provenance.errors import ProvenanceError

from apps.attest import archive as sealed
from apps.sources import archive


class Command(BaseCommand):
    """Подписать опись архива."""

    help = "Подписывает вершину описи архива выпусков ключом службы подписи"

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Сверить файлы с описью, подписать вершину и показать её."""
        # Подписывать опись, не сошедшуюся с файлами, нельзя: подпись закрепила бы подмену.
        # Опись, переписанную после прежних вершин, отклонит сама подпись журнала. Подпись
        # прежней вершины не проверяется: после отзыва ключа опись подписывают заново новым.
        broken = archive.verify_files()
        if broken:
            raise CommandError("Архив не сходится с описью: " + "; ".join(broken))
        try:
            head = sealed.seal()
        except ProvenanceError as error:
            raise CommandError(f"Опись не подписана: {error}") from error
        if head is None:
            raise CommandError(
                "Подписывать нечем или нечего: нужны ключ службы подписи в ATTEST_DIR "
                "и опись архива"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Опись подписана: строк {head.size} вместе с заголовком, вершина {head.root}"
            )
        )
