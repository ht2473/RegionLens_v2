"""Исправление переносов печатной вёрстки и опечаток в названиях разрезов сборника."""

from __future__ import annotations

import json
from functools import lru_cache

from django.conf import settings


@lru_cache(maxsize=1)
def _fragments() -> tuple[tuple[str, str], ...]:
    """Пары «фрагмент источника — исправленный»; длинные первыми, чтобы не задеть их части."""
    path = settings.REFERENCE_DIR / "subsection_typography.json"
    fragments: dict[str, str] = json.loads(path.read_bytes().decode("utf-8"))["fragments"]
    return tuple(sorted(fragments.items(), key=lambda item: -len(item[0])))


def fix_subsection(text: str) -> str:
    """Название разреза без переносов вёрстки и опечаток из справочника."""
    for fragment, fixed in _fragments():
        if fragment in text:
            text = text.replace(fragment, fixed)
    return text
