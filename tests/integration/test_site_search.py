"""
Проверки поиска по сайту: разбор запроса, мерило из ста пятидесяти вопросов, страница
результатов с уточнением «понято как», подсказки и журнал запросов без ответа.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.search import parse as kinds
from apps.search.models import RETENTION_DAYS, SearchMiss
from apps.search.parse import parse

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

BENCHMARK = Path(__file__).resolve().parents[1] / "fixtures" / "search" / "benchmark.json"
# Пороги этапа 2 плана: нужный ряд в первых трёх и верный вид ответа.
SERIES_SHARE = 0.90
KIND_SHARE = 0.85

WAGE = "Y477110378:00"


@pytest.fixture
def content(reference_seed: None) -> None:
    """Глоссарий и методика — по ним отвечают «что такое» и «как считается»."""
    call_command("seed_content", verbosity=0)


def _series_found(row: dict[str, Any], reading: kinds.Reading) -> bool:
    """Нужные ряды среди предложенных: у связи — оба названных, иначе — в первых трёх."""
    candidates = list(reading.series) + [k for k in reading.alternatives if k not in reading.series]
    pool = list(reading.series) if row["kind"] == kinds.RELATION else candidates[:3]
    return all(any(key in pool for key in group) for group in row["series"])


def _kind_found(row: dict[str, Any], reading: kinds.Reading) -> bool:
    """Вид ответа, места, год и термин совпали с ожидаемыми."""
    if reading.kind != row["kind"]:
        return False
    if row["regions"] and not all(
        any(code in reading.codes for code in group) for group in row["regions"]
    ):
        return False
    if row.get("target") and reading.target != row["target"]:
        return False
    return not (row.get("year") and reading.year != row["year"])


class TestBenchmark:
    """Мерило: сто пятьдесят вопросов с ожидаемыми рядами, местами и видом ответа."""

    def test_thresholds(self, content: None) -> None:
        """Ряд в первых трёх — не реже 90 %, вид ответа — не реже 85 %."""
        rows = json.loads(BENCHMARK.read_text(encoding="utf-8"))["queries"]
        with_series = [row for row in rows if row["series"]]
        series_hits = sum(_series_found(row, parse(row["q"])) for row in with_series)
        kind_hits = sum(_kind_found(row, parse(row["q"])) for row in rows)
        assert series_hits / len(with_series) >= SERIES_SHARE
        assert kind_hits / len(rows) >= KIND_SHARE


class TestParse:
    """Разбор запроса по правилам."""

    def test_nickname_and_case(self) -> None:
        """Прозвище и падеж узнаются: «в Питере» — Санкт-Петербург, «в Тыве» — Тыва."""
        assert parse("зарплата в Питере").codes == ["RU-SPE"]
        assert parse("безработица в Тыве").codes == ["RU-TY"]

    def test_preposition_is_not_a_place(self) -> None:
        """Предлог «на» не принят за сокращение НАО."""
        reading = parse("рождаемость на 2020 год")
        assert reading.codes == []
        assert reading.year == 2020

    def test_kinds(self) -> None:
        """Вид ответа по найденному: рейтинг, значение, сравнение, динамика, регион."""
        assert parse("где самые высокие зарплаты").kind == kinds.RANK
        assert parse("зарплата в Татарстане").kind == kinds.VALUE
        assert parse("сравнить Москву и Санкт-Петербург").codes == ["RU-MOW", "RU-SPE"]
        assert parse("как менялась зарплата").kind == kinds.TREND
        assert parse("Татарстан").kind == kinds.REGION

    def test_why_has_no_answer(self) -> None:
        """«Почему» — причин в данных нет: вопрос помечен, вид ответа не выбран."""
        reading = parse("почему падает рождаемость")
        assert reading.why
        assert reading.kind == kinds.NONE

    def test_term_without_question_word(self, content: None) -> None:
        """Термин узнаётся и без «что такое»."""
        reading = parse("разрыв сопоставимости")
        assert reading.kind == kinds.DEFINE
        assert reading.target == "comparability-break"


class TestResultsPage:
    """Страница результатов."""

    def test_empty_query_shows_examples(self, client: Client) -> None:
        """Без запроса — примеры вопросов, журнал не пополняется."""
        response = client.get(reverse("search:results"))
        assert response.status_code == 200
        assert "find-form__hint" in response.content.decode()
        assert not SearchMiss.objects.exists()

    def test_rank_answer_with_chips(self, client: Client, warehouse: Any) -> None:
        """Рейтинг: первые и последние регионы и строка «понято как»."""
        response = client.get(reverse("search:results"), {"q": "где самые высокие зарплаты"})
        body = response.content.decode()
        assert response.context["answer"]["kind"] == kinds.RANK
        assert "understood__chip--kind" in body
        assert "answer-list" in body

    def test_removing_place_turns_value_into_rank(self, client: Client, warehouse: Any) -> None:
        """Снятое место («region=») превращает значение в регионе в рейтинг."""
        query = {"q": "зарплата в Татарстане"}
        assert client.get(reverse("search:results"), query).context["reading"].kind == kinds.VALUE
        refined = client.get(reverse("search:results"), {**query, "region": ""})
        assert refined.context["reading"].kind == kinds.RANK
        assert refined.context["reading"].codes == []

    def test_series_replaced_by_chip(self, client: Client, warehouse: Any) -> None:
        """Чип «или …» заменяет показатель; чужой ключ отбрасывается."""
        response = client.get(
            reverse("search:results"), {"q": "безработица", "series": [WAGE, "нет-такого"]}
        )
        assert response.context["reading"].series == (WAGE,)


class TestMisses:
    """Журнал запросов без ответа."""

    def test_miss_is_recorded(self, client: Client, warehouse: Any) -> None:
        """Запрос без ответа записан без адреса и учётной записи."""
        client.get(reverse("search:results"), {"q": "почему падает рождаемость"})
        miss = SearchMiss.objects.get()
        assert miss.text == "почему падает рождаемость"
        assert miss.language == "ru"

    def test_answered_query_is_not_recorded(self, client: Client, warehouse: Any) -> None:
        """Найденный ответ в журнал не попадает."""
        client.get(reverse("search:results"), {"q": "где самые высокие зарплаты"})
        assert not SearchMiss.objects.exists()

    def test_old_records_are_removed(self) -> None:
        """Записи старше срока хранения убираются при следующей записи."""
        old = SearchMiss.objects.create(text="старый", language="ru")
        SearchMiss.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=RETENTION_DAYS + 1)
        )
        SearchMiss.record("новый", "ru")
        assert list(SearchMiss.objects.values_list("text", flat=True)) == ["новый"]


class TestSuggest:
    """Подсказки быстрого перехода."""

    def test_first_item_leads_to_results(self, client: Client, warehouse: Any) -> None:
        """Первая подсказка — страница результатов с тем же запросом."""
        response = client.get(reverse("search:suggest"), {"q": "зарплата"})
        body = response.content.decode()
        assert response.status_code == 200
        assert reverse("search:results") in body

    def test_short_query_gives_nothing(self, client: Client) -> None:
        """Одной буквы мало: подсказок нет, журнал не пополняется."""
        response = client.get(reverse("search:suggest"), {"q": "з"})
        assert "palette__item" not in response.content.decode()
        assert not SearchMiss.objects.exists()
