"""Упавшие проверки из отчёта JUnit — аннотациями GitHub Actions: сводка запуска видна всем."""

from __future__ import annotations

import sys
from xml.etree import ElementTree

# Длина текста ошибки в аннотации.
MESSAGE_LIMIT = 1500


def escape(text: str) -> str:
    """Экранировать текст команды ``::error``: перевод строки и процент."""
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def escape_property(text: str) -> str:
    """Экранировать значение свойства команды: ещё двоеточие и запятая."""
    return escape(text).replace(":", "%3A").replace(",", "%2C")


def main(path: str) -> int:
    """Напечатать по аннотации на каждую упавшую проверку; вернуть их число."""
    tree = ElementTree.parse(path)  # noqa: S314 - отчёт собственного прогона
    count = 0
    for case in tree.iter("testcase"):
        for problem in [*case.findall("failure"), *case.findall("error")]:
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            text = (problem.get("message") or "") + "\n" + (problem.text or "")
            file = case.get("file") or case.get("classname", "").replace(".", "/") + ".py"
            place = f"file={escape_property(file)},title={escape_property(name)}"
            print(f"::error {place}::{escape(text[:MESSAGE_LIMIT])}")
            count += 1
    return count


if __name__ == "__main__":
    main(sys.argv[1])
