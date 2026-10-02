"""Проверки построчного журнала JSON: кириллица, исключения, произвольные объекты в полях."""

from __future__ import annotations

import json
import logging

import pytest

from apps.core.logging import JSONFormatter

pytestmark = pytest.mark.unit


def make_record(**extra: object) -> logging.LogRecord:
    """Собрать запись журнала с дополнительными полями."""
    record = logging.LogRecord(
        name="regionlens.tests",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="Собран склад: %d наблюдений",
        args=(1969010,),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJSONFormatter:
    """Форматирование записи в одну строку JSON."""

    def test_standard_fields_are_present(self) -> None:
        """Запись содержит время, уровень, логгер и подставленное сообщение."""
        payload = json.loads(JSONFormatter().format(make_record()))

        assert payload["level"] == "INFO"
        assert payload["logger"] == "regionlens.tests"
        assert payload["message"] == "Собран склад: 1969010 наблюдений"
        assert payload["line"] == 42
        assert payload["timestamp"]

    def test_extra_fields_are_added(self) -> None:
        """Поля, переданные через extra, попадают в запись отдельными ключами."""
        payload = json.loads(JSONFormatter().format(make_record(series_key="Y477110461:00")))
        assert payload["series_key"] == "Y477110461:00"

    def test_complex_objects_do_not_break_logging(self) -> None:
        """
        Несериализуемое значение приводится к строке, а не роняет журнал.

        Отказ логирования — худший вид отказа: он скрывает причину основного сбоя.
        """
        payload = json.loads(JSONFormatter().format(make_record(request=object())))
        assert isinstance(payload["request"], str)

    def test_nested_structures_are_preserved(self) -> None:
        """Вложенные списки и словари сохраняют структуру."""
        payload = json.loads(
            JSONFormatter().format(make_record(steps={"facts": [1, 2, 3], "marts": {"ranks": 4}}))
        )
        assert payload["steps"] == {"facts": [1, 2, 3], "marts": {"ranks": 4}}

    def test_exception_is_serialised(self) -> None:
        """Трассировка исключения включается в запись отдельным полем."""
        try:
            raise ValueError("склад не собран")
        except ValueError:
            import sys

            record = make_record()
            record.exc_info = sys.exc_info()

        payload = json.loads(JSONFormatter().format(record))
        assert "ValueError" in payload["exception"]

    def test_cyrillic_is_not_escaped(self) -> None:
        """Кириллица сохраняется как есть: журнал должен читаться человеком."""
        line = JSONFormatter().format(make_record())
        assert "наблюдений" in line
        assert line.count("\n") == 0
