"""
Каталог расчётов своих данных: формула каждой операции разбирается и считается, результаты
сверены с ручным расчётом, применимость к ряду объяснена словами.
"""

from __future__ import annotations

import pytest

from apps.userdata import formulas, operations
from apps.userdata.operations import Info, Values

pytestmark = pytest.mark.unit

TERRITORIES = {"RU": False, "RU-A": True, "RU-B": True}
DISTRICTS = {"RU-A": "FD-1", "RU-B": "FD-1"}
# Отрасли X и Y и их итог T по двум регионам и России за 2019 и 2021 годы.
ROWS: dict[str, list[tuple[str, int, float | None]]] = {
    "u:t:x": [
        ("RU", 2019, 100.0),
        ("RU", 2020, 110.0),
        ("RU", 2021, 120.0),
        ("RU-A", 2019, 10.0),
        ("RU-A", 2020, 12.0),
        ("RU-A", 2021, 15.0),
        ("RU-B", 2019, 30.0),
        ("RU-B", 2021, 30.0),
    ],
    "u:t:y": [
        ("RU", 2019, 300.0),
        ("RU", 2021, 300.0),
        ("RU-A", 2019, 30.0),
        ("RU-A", 2021, 25.0),
        ("RU-B", 2019, 50.0),
        ("RU-B", 2021, 70.0),
    ],
    "u:t:t": [
        ("RU", 2019, 400.0),
        ("RU", 2021, 420.0),
        ("RU-A", 2019, 40.0),
        ("RU-A", 2021, 40.0),
        ("RU-B", 2019, 80.0),
        ("RU-B", 2021, 100.0),
    ],
}
for official in operations.OFFICIAL_INPUTS:
    ROWS[official] = [("RU-A", 2021, 50.0), ("RU-B", 2021, 100.0)]

INFO = {
    "u:t:x": Info("u:t:x", "X", "тыс. человек", is_sum=True, first_year=2019, last_year=2021),
    "u:t:y": Info("u:t:y", "Y", "тыс. человек", is_sum=True, first_year=2019, last_year=2021),
    "u:t:t": Info("u:t:t", "Всего", "тыс. человек", is_sum=True, first_year=2019, last_year=2021),
}


def _values(operation: operations.Operation) -> Values:
    values = Values(info=INFO)
    for item in operation.fields:
        if item.kind == operations.SERIES:
            values.series[item.name] = "u:t:t" if item.name == "b" else "u:t:x"
        elif item.kind == operations.SERIES_LIST:
            values.lists[item.name] = ["u:t:x", "u:t:y"]
        elif item.kind == operations.YEAR:
            values.numbers[item.name] = 2021 if item.name == "last" else 2019
        elif item.kind == operations.WINDOW:
            values.numbers[item.name] = 2
        elif item.kind == operations.NUMBER:
            values.numbers[item.name] = 50
        else:
            values.choices[item.name] = item.choices[0][0]
    return values


def _run(expression: str) -> dict[tuple[str, int], float]:
    outcome = formulas.evaluate(formulas.parse(expression), ROWS, TERRITORIES, DISTRICTS)
    return {(code, year): value for code, year, value in outcome.rows}


@pytest.mark.parametrize(
    "operation",
    [item for item in operations.OPERATIONS if item.outputs],
    ids=lambda item: item.code,
)
def test_every_formula_operation_evaluates(operation: operations.Operation) -> None:
    """Формула операции разбирается и даёт значения; название и единица не пустые."""
    assert operation.outputs is not None
    outputs = operation.outputs(_values(operation))
    assert outputs
    for output in outputs:
        assert output.title
        assert len(output.expression) <= formulas.MAX_LENGTH
        assert _run(output.expression), output.expression


def test_recount_operations_name_existing_recounts() -> None:
    """Пересчёты каталога — те, что умеет сборка."""
    from apps.userdata import build

    known = set(build.PER_SUFFIXES) | set(build.RECALC_SUFFIXES)
    assert set(operations.recount_codes()) <= known


def _single(code: str, **choices: str) -> dict[tuple[str, int], float]:
    operation = operations.BY_CODE[code]
    values = _values(operation)
    values.choices.update(choices)
    assert operation.outputs is not None
    return _run(operation.outputs(values)[0].expression)


def test_change_operations() -> None:
    assert _single("baseyear")[("RU-A", 2021)] == pytest.approx(150)
    assert _single("between")[("RU-A", 2021)] == pytest.approx(50)
    assert _single("between", mode="absolute")[("RU-A", 2021)] == pytest.approx(5)
    assert _single("annualrate")[("RU-A", 2021)] == pytest.approx((1.5**0.5 - 1) * 100)


def test_structure_and_regional_methods() -> None:
    # Коэффициент локализации: (10 / 40) / (100 / 400) = 1.
    assert _single("lq")[("RU-A", 2019)] == pytest.approx(1)
    # Херфиндаль — Хиршман: доли X и Y в итоге A за 2019 — 0,25 и 0,75.
    assert _single("hhi")[("RU-A", 2019)] == pytest.approx((0.25**2 + 0.75**2) * 10000)
    shift = _single("structuralshift")[("RU-A", 2021)]
    # Доли 0,25 → 0,375 и 0,75 → 0,625: квадратичное среднее изменений — 12,5 п. п.
    assert shift == pytest.approx(12.5)


def test_shift_share_adds_up_to_the_change() -> None:
    """Три части сдвига-доли в сумме дают изменение суммы отраслей региона."""
    operation = operations.BY_CODE["shiftshare"]
    assert operation.outputs is not None
    parts = [_run(item.expression) for item in operation.outputs(_values(operation))]
    for code, change in (("RU-A", (15 + 25) - (10 + 30)), ("RU-B", (30 + 70) - (30 + 50))):
        assert sum(part[(code, 2021)] for part in parts) == pytest.approx(change)


def test_reason_explains_mismatch() -> None:
    relative = Info("k", "Доля", "%", is_sum=False, first_year=2020, last_year=2020, regions=85)
    assert "сумм" in operations.reason(operations.BY_CODE["per1000"], relative)
    assert "лет" in operations.reason(operations.BY_CODE["baseyear"], relative)
    assert "денежн" in operations.reason(operations.BY_CODE["subsistence"], relative)
    money = Info("k", "Доход", "руб.", is_currency=True, is_rubles=True, regions=85)
    assert operations.reason(operations.BY_CODE["subsistence"], money) == ""
    missing = dict.fromkeys(operations.OFFICIAL_INPUTS, False)
    assert "официальных" in operations.reason(operations.BY_CODE["subsistence"], money, missing)


def _warn(expression: str, **items: Info) -> list[str]:
    return operations.warnings(expression, {item.key: item for item in items.values()})


def test_warnings_about_sums() -> None:
    people = Info("u:t:p", "Население — Всего", "тыс. человек", is_sum=True)
    men = Info("u:t:m", "Население — мужчины", "тыс. человек", is_sum=True)
    share = Info("u:t:s", "Доля пенсионеров", "%")
    persons = Info("u:t:q", "Врачи", "человек", is_sum=True)
    found = _warn("{u:t:p} + {u:t:m}", p=people, m=men)
    assert any("дважды" in item for item in found)
    assert any("относительн" in item for item in _warn("{u:t:p} + {u:t:s}", p=people, s=share))
    assert any(
        "разных единицах" in item for item in _warn("{u:t:p} + {u:t:q}", p=people, q=persons)
    )
    industry = Info("u:t:a", "Добавленная стоимость: промышленность", "млн руб.", is_sum=True)
    trade = Info("u:t:b", "Добавленная стоимость: торговля", "млн руб.", is_sum=True)
    assert any("не ВРП" in item for item in _warn("{u:t:a} + {u:t:b}", a=industry, b=trade))
    # Одна единица, суммы без итога — молча.
    assert _warn("{u:t:a} * 2 + 1", a=industry) == []


def test_warning_about_years() -> None:
    crimes = Info("u:t:c", "Преступления", "единиц", is_sum=True, first_year=2015, last_year=2023)
    children = Info("u:t:k", "Дети", "тыс. человек", is_sum=True, first_year=2023, last_year=2023)
    found = _warn("{u:t:c} / {u:t:k}", c=crimes, k=children)
    assert any("2023–2023" in item for item in found)
    # Сдвиг по годам — деление намеренное.
    assert _warn("{u:t:c} / ВГОДУ({u:t:c}; 2015)", c=crimes) == []
