"""
Формулы своих данных: разбор, сопоставление ссылок, вычисление в замкнутом соединении
и устойчивость разборщика к случайным строкам.
"""

from __future__ import annotations

import math
import random
import re
from typing import Any

import pytest

from apps.userdata import formulas
from apps.userdata.formulas import Candidate, Directory, FormulaError

TERRITORIES = {"RU": False, "RU-A": True, "RU-B": True, "RU-C": True}


def _directory() -> Directory:
    directory = Directory()
    directory.add(Candidate("u:t:dtp", "ДТП", "this"), ["ДТП", "Дорожно-транспортные происшествия"])
    directory.add(Candidate("u:t:cars", "Автомобили", "this"), ["Автомобили"])
    directory.add(Candidate("u:o:cars", "Автомобили", "table", table="Парк машин"), ["Автомобили"])
    directory.add(Candidate("u:o:roads", "Дороги", "table", table="Парк машин"), ["Дороги"])
    directory.add(
        Candidate("Y1:00", "Численность населения", "official"), ["Численность населения"]
    )
    directory.add(Candidate("Y2:00", "Доход", "official"), ["Доход"])
    directory.add(Candidate("u:o:income", "Доход", "table", table="Парк машин"), ["Доход"])
    return directory


def _compute(
    text: str, inputs: dict[str, list[tuple[str, int, float | None]]]
) -> dict[tuple[str, int], float]:
    expression, _keys = formulas.canonical(text, _directory())
    outcome = formulas.evaluate(formulas.parse(expression), inputs, TERRITORIES)
    return {(code, year): value for code, year, value in outcome.rows}


INPUTS: dict[str, list[tuple[str, int, float | None]]] = {
    "u:t:dtp": [
        ("RU-A", 2020, 10.0),
        ("RU-A", 2021, 12.0),
        ("RU-B", 2020, 5.0),
        ("RU-B", 2021, 0.0),
        ("RU-C", 2021, 7.0),
    ],
    "u:t:cars": [
        ("RU-A", 2020, 1000.0),
        ("RU-A", 2021, 0.0),
        ("RU-B", 2020, 500.0),
        ("RU-B", 2021, 400.0),
        ("RU-C", 2021, 70.0),
    ],
    "Y1:00": [("RU-A", 2021, 2.0), ("RU-B", 2021, -1.0), ("RU-C", 2021, 4.0)],
}


class TestParse:
    """Приоритет действий, функции, ошибки с местом."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1 + 2 * 3", 7),
            ("(1 + 2) * 3", 9),
            ("2 ^ 3 ^ 2", 512),
            ("-2 ^ 2", -4),
            ("2 ^ -1", 0.5),
            ("10 / 4", 2.5),
            ("1,5 * 2", 3),
            ("ROUND(2.345; 2)", 2.35),
            ("округл(2.345, 1)", 2.3),
            ("MAX(1; 5; 3) - MIN(4, 2)", 3),
            ("ABS(-3) + SQRT(16) + LN(EXP(1)) + LOG10(100)", 10),
        ],
    )
    def test_arithmetic(self, text: str, expected: float) -> None:
        values = _compute(f"{text} + [ДТП] * 0", INPUTS)
        assert values[("RU-A", 2020)] == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "Формула пустая"),
            ("[ДТП] +", "обрывается"),
            ("([ДТП] + 1", "Не закрыта скобка"),
            ("[ДТП] + 1)", "Неожиданное «)»"),
            ("[ДТП", "Нет закрывающей скобки"),
            ("[] + 1", "Пустая ссылка"),
            ("СУММ([ДТП])", "Нет функции «СУММ»"),
            ("ДТП / 2", "Нет функции «ДТП»"),
            ("AGO([ДТП])", "аргументов должно быть 2"),
            ("AGO([ДТП], 1.5)", "целое число от 1 до 50"),
            ("AGO([ДТП], 0)", "целое число от 1 до 50"),
            ("ROUND([ДТП], 11)", "целое число от 0 до 10"),
            ("MAX([ДТП])", "от 2 до 8"),
            ("[ДТП] # 2", "Непонятный знак «#»"),
            ("1 + 2", "нет ни одного показателя"),
            ("[Пешеходы] / 2", "Нет показателя «Пешеходы»"),
            ("[Доход] / 2", "подходит к нескольким рядам"),
            ("{u:x:missing} / 2", "Нет показателя с ключом"),
            ("1" * 21 + " + [ДТП]", "Слишком длинное число"),
            ("[ДТП]" + " + 1" * 600, "длиннее 2000 знаков"),
            ("(" * 25 + "[ДТП]" + ")" * 25, "Слишком глубокая вложенность"),
        ],
    )
    def test_errors(self, text: str, message: str) -> None:
        with pytest.raises(FormulaError, match=re.escape(message)):
            formulas.canonical(text, _directory())

    def test_own_table_wins_over_other(self) -> None:
        # «Автомобили» есть и здесь, и в другой таблице: своя — без вопроса.
        expression, keys = formulas.canonical("[ДТП] / [Автомобили] * 1000", _directory())
        assert expression == "{u:t:dtp} / {u:t:cars} * 1000"
        assert keys == ["u:t:dtp", "u:t:cars"]

    def test_qualified_and_keyed(self) -> None:
        expression, keys = formulas.canonical(
            "[Парк машин: Автомобили] + {Y1:00} + [дорожно–транспортные  происшествия]",
            _directory(),
        )
        assert keys == ["u:o:cars", "Y1:00", "u:t:dtp"]
        titles = {"u:o:cars": "Парк машин: Автомобили", "Y1:00": "Численность", "u:t:dtp": "ДТП"}
        assert (
            formulas.display(expression, titles)
            == "[Парк машин: Автомобили] + [Численность] + [ДТП]"
        )
        assert formulas.display("{gone:key} * 2", {}) == "{gone:key} * 2"
        assert formulas.keys_of(expression) == keys


class TestEvaluate:
    """Значения, пропуски, деление на ноль, сдвиг по годам, ранг."""

    def test_ratio_and_zero_division(self) -> None:
        expression, _keys = formulas.canonical("[ДТП] / [Автомобили] * 1000", _directory())
        outcome = formulas.evaluate(formulas.parse(expression), INPUTS, TERRITORIES)
        values = {(code, year): value for code, year, value in outcome.rows}
        assert values[("RU-A", 2020)] == pytest.approx(10)
        assert values[("RU-B", 2021)] == pytest.approx(0)
        # У RU-A в 2021 году автомобилей ноль: значение пусто и названо.
        assert ("RU-A", 2021) not in values
        assert outcome.zero_division == 1
        assert outcome.warnings == ["Субъектов с делением на ноль: 1 — их значения пусты."]
        assert outcome.regions == 3
        assert outcome.years == (2020, 2021)

    def test_domain_errors_give_gaps(self) -> None:
        values = _compute("LN([Численность населения]) + SQRT([Численность населения])", INPUTS)
        assert ("RU-B", 2021) not in values
        assert values[("RU-C", 2021)] == pytest.approx(math.log(4) + 2)
        assert ("RU-A", 2021) in values
        # Степень отрицательного числа в дробной степени — пропуск, а не ошибка.
        assert ("RU-B", 2021) not in _compute("[Численность населения] ^ 0.5", INPUTS)
        assert ("RU-A", 2021) not in _compute("EXP([ДТП] * 1000)", INPUTS)

    def test_ago_and_growth(self) -> None:
        values = _compute("[ДТП] / AGO([ДТП], 1) * 100", INPUTS)
        assert values == {("RU-A", 2021): pytest.approx(120), ("RU-B", 2021): pytest.approx(0)}
        nested = _compute(
            "AGO(AGO([ДТП], 1) + 1, 1) + [ДТП] * 0",
            {"u:t:dtp": [("RU-A", year, float(year - 2000)) for year in range(2018, 2023)]},
        )
        assert nested[("RU-A", 2022)] == pytest.approx(21)

    def test_rank(self) -> None:
        values = _compute("RANK([Автомобили])", INPUTS)
        assert values[("RU-B", 2021)] == 1
        assert values[("RU-C", 2021)] == 2
        assert values[("RU-A", 2021)] == 3
        assert values[("RU-A", 2020)] == 1

    def test_min_max_need_all_values(self) -> None:
        values = _compute("MAX([ДТП], [Численность населения])", INPUTS)
        assert values == {
            ("RU-A", 2021): pytest.approx(12),
            ("RU-B", 2021): pytest.approx(0),
            ("RU-C", 2021): pytest.approx(7),
        }


# Значения для условий, лет, России и округов: субъекты A и B — в одном округе, C — в другом.
SERIES: dict[str, list[tuple[str, int, float | None]]] = {
    "u:t:dtp": [
        ("RU", 2019, 100.0),
        ("RU", 2020, 120.0),
        ("RU", 2021, 150.0),
        ("RU-A", 2019, 10.0),
        ("RU-A", 2020, 20.0),
        ("RU-A", 2021, 30.0),
        ("RU-B", 2019, 40.0),
        ("RU-B", 2020, None),
        ("RU-B", 2021, 60.0),
        ("RU-C", 2019, 70.0),
        ("RU-C", 2020, 80.0),
        ("RU-C", 2021, 90.0),
    ],
    "u:t:cars": [
        ("RU-A", 2021, 1.0),
        ("RU-B", 2021, 3.0),
        ("RU-C", 2021, 2.0),
    ],
}
DISTRICTS = {"RU-A": "FD-1", "RU-B": "FD-1", "RU-C": "FD-2"}


def _values(text: str) -> dict[tuple[str, int], float]:
    expression, _keys = formulas.canonical(text, _directory())
    outcome = formulas.evaluate(formulas.parse(expression), SERIES, TERRITORIES, DISTRICTS)
    return {(code, year): value for code, year, value in outcome.rows}


class TestConditionsAndWindows:
    """Условия, пропуски, годы, Россия, субъекты и округ, окна по годам."""

    def test_if_with_comparison(self) -> None:
        found = _values("ЕСЛИ([ДТП] > 50; 1; 0)")
        assert found[("RU-A", 2021)] == 0
        assert found[("RU-C", 2021)] == 1
        # Пропуск в условии — пропуск результата, а не ветка «иначе».
        assert ("RU-B", 2020) not in found

    def test_and_or_not_isna(self) -> None:
        found = _values("IF(AND([ДТП] >= 20; [ДТП] <= 60); 1; 0)")
        assert found[("RU-A", 2020)] == 1 and found[("RU-C", 2021)] == 0
        assert _values("ЕСЛИ(НЕ([ДТП] <> 30); 5; 6)")[("RU-A", 2021)] == 5
        assert _values("ЕСЛИ(ЕПУСТО([ДТП]); -1; [ДТП])")[("RU-B", 2020)] == -1
        assert _values("ЕСЛИ(ИЛИ([ДТП] = 10; [ДТП] = 90); 1; 0)")[("RU-C", 2021)] == 1

    def test_ifna(self) -> None:
        found = _values("ЕСЛИПУСТО([ДТП]; 0)")
        assert found[("RU-B", 2020)] == 0 and found[("RU-B", 2021)] == 60

    def test_base_year_and_one_year(self) -> None:
        found = _values("[ДТП] / ВГОДУ([ДТП]; 2019) * 100")
        assert found[("RU-A", 2021)] == pytest.approx(300)
        assert found[("RU-C", 2019)] == pytest.approx(100)
        change = _values("ЗАГОД([ДТП] - ВГОДУ([ДТП]; 2019); 2021)")
        assert change == {
            ("RU", 2021): 50.0,
            ("RU-A", 2021): 20.0,
            ("RU-B", 2021): 20.0,
            ("RU-C", 2021): 20.0,
        }

    def test_russia_total_mean_stdev(self) -> None:
        assert _values("[ДТП] - РОССИЯ([ДТП])")[("RU-A", 2021)] == pytest.approx(-120)
        assert _values("[ДТП] / ИТОГ([ДТП]) * 100")[("RU-A", 2021)] == pytest.approx(30 / 180 * 100)
        found = _values("([ДТП] - СРЕДНЕЕ([ДТП])) / СТАНДОТКЛОН([ДТП])")
        assert found[("RU-B", 2021)] == pytest.approx(0)
        assert found[("RU-C", 2021)] == pytest.approx(1)

    def test_district_means(self) -> None:
        found = _values("[ДТП] - ОКРУГ([ДТП])")
        assert found[("RU-A", 2021)] == pytest.approx(-15)
        assert found[("RU-C", 2021)] == pytest.approx(0)
        weighted = _values("ВЗВОКРУГ([ДТП]; [Автомобили])")
        assert weighted[("RU-A", 2021)] == pytest.approx((30 * 1 + 60 * 3) / 4)
        assert weighted[("RU-C", 2021)] == pytest.approx(90)

    def test_moving_and_cumulative(self) -> None:
        moving = _values("СКОЛЬЗЯЩЕЕ([ДТП]; 2)")
        assert moving[("RU-A", 2020)] == pytest.approx(15)
        assert ("RU-A", 2019) not in moving
        # Окно с пропуском — без значения.
        assert ("RU-B", 2020) not in moving and ("RU-B", 2021) not in moving
        total = _values("НАКОПЛЕННОЕ([ДТП])")
        assert total[("RU-A", 2021)] == pytest.approx(60)
        assert total[("RU-B", 2019)] == pytest.approx(40)
        # После пропуска накопленный итог не продолжается.
        assert ("RU-B", 2021) not in total

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("[ДТП] > 1", "только в условии"),
            ("ЕСЛИ([ДТП]; 1; 0)", "нужно условие"),
            ("[ДТП] + И([ДТП] > 1; [ДТП] < 3)", "условие"),
            ("ВГОДУ([ДТП]; 1700)", "целое число от 1900"),
            ("СКОЛЬЗЯЩЕЕ([ДТП]; 1)", "целое число от 2"),
        ],
    )
    def test_condition_errors(self, text: str, message: str) -> None:
        with pytest.raises(FormulaError, match=message):
            formulas.canonical(text, _directory())


class TestFuzz:
    """Перебор случайных строк: разбор либо удаётся, либо отвечает понятной ошибкой."""

    ALPHABET = list("0123456789.,;+-*/^() []{}ABCDEFGHIJKLMNOPQRSTUVWXYZ_абвгдДТПaz#$%\"'\\\n\t")
    WORDS = [
        "[ДТП]",
        "[Автомобили]",
        "{u:t:dtp}",
        "AGO(",
        "RANK(",
        "ЕСЛИ(",
        "И(",
        "ВГОДУ(",
        "РОССИЯ(",
        "СКОЛЬЗЯЩЕЕ(",
        ">",
        "<=",
        "<>",
        "=",
        ";",
        "ROUND(",
        "MAX(",
        "1",
        "2.5",
        ")",
        ",",
        "^",
        "-",
        "/",
        "(",
        "[Доход]",
        "[",
        "]",
        "{",
        "}",
    ]

    def test_random_characters(self) -> None:
        generator = random.Random(20261004)
        for _ in range(3000):
            text = "".join(generator.choice(self.ALPHABET) for _ in range(generator.randint(0, 60)))
            self._check(text)

    def test_random_tokens(self) -> None:
        generator = random.Random(4102026)
        for _ in range(3000):
            text = " ".join(generator.choice(self.WORDS) for _ in range(generator.randint(1, 25)))
            self._check(text)

    def test_random_trees_evaluate(self) -> None:
        generator = random.Random(1789)
        leaves = ["[ДТП]", "[Автомобили]", "[Численность населения]", "0", "1", "-2.5", "1000"]

        def tree(depth: int) -> str:  # noqa: PLR0911 — по ветви на вид узла
            if depth == 0 or generator.random() < 0.3:
                return generator.choice(leaves)
            choice = generator.randint(0, 9)
            if choice == 0:
                return f"({tree(depth - 1)} {generator.choice('+-*/^')} {tree(depth - 1)})"
            if choice == 1:
                return f"-{tree(depth - 1)}"
            if choice == 2:
                return f"AGO({tree(depth - 1)}, {generator.randint(1, 3)})"
            if choice == 3:
                return f"RANK({tree(depth - 1)})"
            if choice == 4:
                return (
                    f"{generator.choice(['LN', 'SQRT', 'EXP', 'ABS', 'LOG10'])}({tree(depth - 1)})"
                )
            if choice == 5:
                return f"MAX({tree(depth - 1)}; {tree(depth - 1)})"
            if choice == 6:
                return f"ЕСЛИ({tree(depth - 1)} > {tree(depth - 1)}; {tree(depth - 1)}; 0)"
            if choice == 7:
                return f"ВГОДУ({tree(depth - 1)}; {generator.choice([2020, 2021])})"
            if choice == 8:
                return (
                    f"{generator.choice(['РОССИЯ', 'ИТОГ', 'СРЕДНЕЕ', 'ОКРУГ'])}({tree(depth - 1)})"
                )
            return f"ROUND({tree(depth - 1)}, {generator.randint(0, 3)})"

        for _ in range(300):
            text = tree(4)
            try:
                expression, _keys = formulas.canonical(text, _directory())
            except FormulaError:
                continue
            outcome = formulas.evaluate(formulas.parse(expression), INPUTS, TERRITORIES)
            assert all(math.isfinite(value) for _code, _year, value in outcome.rows)

    def _check(self, text: str) -> Any:
        try:
            return formulas.canonical(text, _directory())
        except FormulaError as error:
            assert str(error)
            return None
