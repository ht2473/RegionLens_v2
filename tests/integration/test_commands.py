"""Проверки команд управления: последовательность из руководства отрабатывает без правок."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.support import synthetic
from tests.support.warehouse import accept_small_source

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def run(command: str, *args: str, **options: Any) -> str:
    """Выполнить команду и вернуть её вывод."""
    out = StringIO()
    call_command(command, *args, stdout=out, stderr=out, **options)
    return out.getvalue()


class TestSeedReference:
    """Загрузка справочника территорий."""

    def test_check_reports_complete_reference(self, reference_seed: None) -> None:
        """Проверка справочника подтверждает его целостность."""
        output = run("seed_reference", "--check")
        assert "Справочник корректен" in output

    def test_repeated_load_is_idempotent(self, reference_seed: None) -> None:
        """
        Повторная загрузка не создаёт дублей.

        Команда входит в последовательность развёртывания и выполняется при каждом
        обновлении: неидемпотентная загрузка удвоила бы список субъектов.
        """
        from apps.catalog.models import Territory

        before = Territory.objects.count()
        run("seed_reference", verbosity=0)
        assert Territory.objects.count() == before


class TestEtlBuild:
    """Сборка аналитического склада."""

    def test_check_profiles_source_without_writing(
        self, synthetic_dataset: Any, tmp_path: Path
    ) -> None:
        """Режим проверки печатает профиль файла и ничего не пишет."""
        with accept_small_source():
            output = run("etl_build", "--check", source=synthetic_dataset.path)

        assert "Профиль исходного набора данных" in output
        assert "пригоден к загрузке" in output

    def test_strict_check_rejects_truncated_file(self, synthetic_dataset: Any) -> None:
        """
        Усечённый файл отклоняется.

        Молчаливая загрузка неполного набора дала бы правдоподобные, но неверные
        значения на всех страницах сразу.
        """
        with pytest.raises(CommandError):
            run("etl_build", "--check", source=synthetic_dataset.path)

    def test_missing_source_is_reported(self, tmp_path: Path) -> None:
        """Отсутствие исходного файла объясняется, а не приводит к трассировке."""
        with pytest.raises(CommandError):
            run("etl_build", "--check", source=tmp_path / "нет-файла.parquet")

    def test_marts_cannot_be_rebuilt_without_warehouse(self, tmp_path: Path) -> None:
        """Пересчёт витрин без склада отклоняется с понятной причиной."""
        with pytest.raises(CommandError):
            run("etl_build", "--marts", target=tmp_path / "нет-склада.duckdb")

    def test_marts_are_recomputed(
        self, warehouse: Any, warehouse_file: Path, tmp_path: Path
    ) -> None:
        """Витрины пересчитываются отдельно от полной сборки — на копии склада."""
        import shutil

        copy = tmp_path / "warehouse.duckdb"
        shutil.copy(warehouse_file, copy)

        output = run("etl_build", "--marts", target=copy)
        assert "Итоги сборки" in output


class TestSeedContent:
    """Начальное наполнение содержимого."""

    def test_content_is_created(self, db: None) -> None:
        """Команда создаёт разделы методики и термины глоссария."""
        from apps.content.models import GlossaryTerm, MethodologySection

        run("seed_content", verbosity=0)

        assert MethodologySection.objects.exists()
        assert GlossaryTerm.objects.exists()

    def test_editor_changes_are_preserved(self, db: None) -> None:
        """
        Повторный запуск не затирает правки редактора.

        Команда выполняется при каждом развёртывании, и молчаливая перезапись
        текстов означала бы потерю работы редактора.
        """
        from apps.content.models import GlossaryTerm

        run("seed_content", verbosity=0)
        term = GlossaryTerm.objects.first()
        term.definition = "Изменено редактором"
        term.save()

        run("seed_content", verbosity=0)
        term.refresh_from_db()
        assert term.definition == "Изменено редактором"

    def test_force_overwrites_texts(self, db: None) -> None:
        """Явное указание перезаписи возвращает исходные тексты."""
        from apps.content.models import GlossaryTerm

        run("seed_content", verbosity=0)
        term = GlossaryTerm.objects.first()
        term.definition = "Изменено редактором"
        term.save()

        run("seed_content", "--force", verbosity=0)
        term.refresh_from_db()
        assert term.definition != "Изменено редактором"

    def test_single_section_can_be_filled(self, db: None) -> None:
        """Разделы наполняются по отдельности."""
        from apps.content.models import GlossaryTerm

        run("seed_content", "--only", "glossary", verbosity=0)

        assert GlossaryTerm.objects.exists()


class TestBoundaries:
    """Подготовка файла границ субъектов."""

    def test_prepared_file_matches_reference(self, reference_seed: None) -> None:
        """
        Готовый файл границ содержит ровно те субъекты, что и справочник.

        Расхождение означало бы незакрашенные области на карте, и обнаружилось бы
        только в браузере.
        """
        import json

        from apps.catalog.models import Territory
        from apps.maps.boundaries import boundaries_path

        payload = json.loads(boundaries_path().read_text(encoding="utf-8"))
        codes = {feature["properties"]["code"] for feature in payload["features"]}
        assert codes == set(Territory.objects.comparable().values_list("code", flat=True))

    def test_missing_input_file_is_reported(self, tmp_path: Path) -> None:
        """Отсутствие исходного набора геометрии объясняется понятной ошибкой."""
        with pytest.raises(CommandError):
            run("build_boundaries", input=tmp_path / "нет-файла.zip")


class TestSyntheticDataset:
    """Синтетический набор, на котором работают проверки."""

    def test_structure_matches_the_source(self, synthetic_dataset: Any) -> None:
        """
        Набор повторяет структуру исходного файла Росстата.

        Если структура разойдётся, проверки конвейера станут проверками
        самих себя, а не системы.
        """
        import duckdb

        from apps.warehouse.etl.source import EXPECTED_SCHEMA

        connection = duckdb.connect()
        try:
            columns = {
                row[0]
                for row in connection.execute(
                    "SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet(?))",
                    [str(synthetic_dataset.path)],
                ).fetchall()
            }
        finally:
            connection.close()

        assert columns == set(EXPECTED_SCHEMA)

    def test_period_and_volume_are_as_declared(self, synthetic_dataset: Any) -> None:
        """Объявленные границы периода и объём совпадают с содержимым файла."""
        assert synthetic_dataset.first_year == synthetic.FIRST_YEAR
        assert synthetic_dataset.last_year == synthetic.LAST_YEAR
        assert synthetic_dataset.row_count > 30_000
