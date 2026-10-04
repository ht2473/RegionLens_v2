"""Каталоги таблиц гостей — для исключения из резервной копии: ``userdata_guest_dirs``."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.userdata.models import Dataset


class Command(BaseCommand):
    """Напечатать пути каталогов таблиц гостей относительно корня приложения, по одному в строке."""

    help = "Каталоги таблиц без учётной записи: они хранятся сутки и в копию не входят"

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        root = settings.USERDATA_DIR.relative_to(settings.BASE_DIR).as_posix()
        for public_id in Dataset.objects.filter(owner__isnull=True).values_list(
            "public_id", flat=True
        ):
            self.stdout.write(f"{root}/{public_id}")
