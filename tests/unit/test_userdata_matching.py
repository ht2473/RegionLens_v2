"""Сопоставление подписей территорий в таблицах пользователей со справочником."""

from __future__ import annotations

import pytest

from apps.userdata import matching
from apps.userdata.matching import ASK, EXACT, FIXED, MUNICIPAL, NESTED, NONE, OUTSIDE, match

# Написания «от руки» из проверки 03.10.2026: краткие и разговорные формы, сокращения,
# опечатки, английские названия, коды.
HANDWRITTEN = [
    ("Татарстан", "RU-TA"),
    ("Башкирия", "RU-BA"),
    ("Чувашия", "RU-CU"),
    ("Удмуртия", "RU-UD"),
    ("Марий Эл", "RU-ME"),
    ("Коми", "RU-KO"),
    ("Якутия", "RU-SA"),
    ("Саха (Якутия)", "RU-SA"),
    ("Тыва", "RU-TY"),
    ("Тува", "RU-TY"),
    ("Чечня", "RU-CE"),
    ("Северная Осетия", "RU-SE"),
    ("Кабардино-Балкария", "RU-KB"),
    ("Карачаево-Черкесия", "RU-KC"),
    ("Крым", "RU-CR"),
    ("Чукотка", "RU-CHU"),
    ("Кузбасс", "RU-KEM"),
    ("Югра", "RU-KHM"),
    ("Приморье", "RU-PRI"),
    ("Кубань", "RU-KDA"),
    ("Подмосковье", "RU-MOS"),
    ("Петербург", "RU-SPE"),
    ("Ленобласть", "RU-LEN"),
    ("Респ. Татарстан", "RU-TA"),
    ("Респ Башкортостан", "RU-BA"),
    ("Р. Саха (Якутия)", "RU-SA"),
    ("Республика Алтай", "RU-AL"),
    ("Алтайский край", "RU-ALT"),
    ("КБР", "RU-KB"),
    ("ХМАО — Югра", "RU-KHM"),
    ("Ханты-Мансийский АО", "RU-KHM"),
    ("ЯНАО", "RU-YAN"),
    ("НАО", "RU-NEN"),
    ("Еврейская АО", "RU-YEV"),
    ("Еврейская авт. обл.", "RU-YEV"),
    ("Чукотский АО", "RU-CHU"),
    ("г.Москва", "RU-MOW"),
    ("Москва г.", "RU-MOW"),
    ("МОСКВА", "RU-MOW"),
    ("СПб", "RU-SPE"),
    ("С.-Петербург", "RU-SPE"),
    ("Московская обл", "RU-MOS"),
    ("Кемеровская область - Кузбасс", "RU-KEM"),
    ("Пермский кр", "RU-PER"),
    ("Республика Адыгея (Адыгея)", "RU-AD"),
    ("Москва 1)", "RU-MOW"),
    ("Москва²", "RU-MOW"),
    ("Moscow", "RU-MOW"),
    ("Moscow Region", "RU-MOS"),
    ("St. Petersburg", "RU-SPE"),
    ("Republic of Tatarstan", "RU-TA"),
    ("Tatarstan", "RU-TA"),
    ("Sakha (Yakutia) Republic", "RU-SA"),
    ("Khanty-Mansi Autonomous Okrug – Yugra", "RU-KHM"),
    ("Chechnya", "RU-CE"),
    ("Altai Krai", "RU-ALT"),
    ("Altai Republic", "RU-AL"),
    ("Kemerovo Oblast", "RU-KEM"),
    ("Primorsky Krai", "RU-PRI"),
    ("RU-TA", "RU-TA"),
    ("92000000", "RU-TA"),
    ("45000000", "RU-MOW"),
    ("45000000.0", "RU-MOW"),
    ("Российская Федерация", "RU"),
    ("Россия", "RU"),
    ("РФ", "RU"),
    ("Всего по России", "RU"),
    ("ЦФО", "FD-CFO"),
    ("Северо-Кавказский ФО", "FD-SKFO"),
    ("Дальневосточный ФО", "FD-DFO"),
]
# Подписи из таблиц Росстата: латиница в русских словах, слипшиеся слова, сноски внутри.
FROM_TABLES = [
    ("Калинингpадская область", "RU-KGD"),
    ("Hовгородская область", "RU-NGR"),
    ("г. Cанкт-Петербург", "RU-SPE"),
    ("Северо-Кавказскийфедеральный округ", "FD-SKFO"),
    ("Ханты-Мансийскийавтономный округ– Югра", "RU-KHM"),
    ("Архангельская областьбез автономного округа", "RU-ARK"),
    ("Тюменская область (кроме Ханты-Мансийского АО-Югры и Ямало-Ненецкого АО)", "RU-TYU"),
    ("Сибирский2) федеральный округ", "FD-SFO"),
    ("Южный федеральный округ2); 3)", "FD-YUFO"),
    ("Ямало-Ненецкий автономный округ (Тюменская область)", "RU-YAN"),
    ("в т.ч. Ненецкий АО", "RU-NEN"),
    ("Архангельская область и Ненецкий автономный округ3)", "RU-ARK-AGG"),
    ("Дальневосточный 2", "FD-DFO"),
    ("Российская Федерация, млн т", "RU"),
]
TYPOS = [
    ("Нижегородская облать", "RU-NIZ"),
    ("Калинингадская область", "RU-KGD"),
    ("Красноярский карй", "RU-KYA"),
    ("Респулика Карелия", "RU-KR"),
    ("Северо-Кавказкий федеральный округ", "FD-SKFO"),
    ("Ямало-Ненецкий втономный округ", "RU-YAN"),
]


class TestSingleLabels:
    """Одна подпись: от точного совпадения до исправленного написания."""

    @pytest.mark.parametrize(("label", "code"), HANDWRITTEN + FROM_TABLES)
    def test_known_without_question(self, label: str, code: str) -> None:
        found = match(label)
        assert found.kind == EXACT
        assert found.code == code

    @pytest.mark.parametrize(("label", "code"), TYPOS)
    def test_typos_are_fixed_and_marked(self, label: str, code: str) -> None:
        found = match(label)
        assert found.kind == FIXED
        assert found.code == code
        assert found.detail("similarity") >= matching.FUZZY_ACCEPT

    @pytest.mark.parametrize("label", ["Алтай", "Altai"])
    def test_ambiguous_names_are_asked(self, label: str) -> None:
        found = match(label)
        assert found.kind == ASK
        assert set(found.candidates) == {"RU-AL", "RU-ALT"}
        assert not found.is_resolved

    @pytest.mark.parametrize(
        ("label", "reason"),
        [
            ("Пермская область", "merged"),
            ("Читинская область", "merged"),
            ("Таймырский (Долгано-Ненецкий) автономный округ", "merged"),
            ("ДНР", "new"),
            ("Запорожская область", "new"),
            ("Крымский федеральный округ", "district"),
            ("Санкт-Петербург и Ленинградская область", "composite"),
            ("Республика Крым и Севастополь", "composite"),
            ("Гл. мед. упр. Управления делами Президента РФ", "organization"),
        ],
    )
    def test_outside_reference_with_reason(self, label: str, reason: str) -> None:
        found = match(label)
        assert found.kind == OUTSIDE
        assert found.reason == reason
        assert found.code is None

    def test_merged_subject_names_year_and_successor(self) -> None:
        found = match("Коми-Пермяцкий АО")
        assert found.detail("year") == 2005
        assert found.detail("into") == "RU-PER"

    @pytest.mark.parametrize(
        "label",
        [
            "в том числе:",
            "Итого",
            "2016",
            "федеральный округ",
            "без автономного округа",
            "1) Без учета статистической информации по Донецкой Народной Республике",
            "——— 1) По данным Федерального агентства лесного хозяйства.",
            "1 Данные за 2025 г. - предварительные данные",
            "",
        ],
    )
    def test_not_territories(self, label: str) -> None:
        assert match(label).kind == NONE

    @pytest.mark.parametrize("label", ["Казань", "Ленинский район", "г. Тольятти"])
    def test_cities_and_districts_are_named(self, label: str) -> None:
        assert match(label).kind == MUNICIPAL

    def test_capital_suggests_its_region(self) -> None:
        assert match("Казань").candidates == ("RU-TA",)


class TestNested:
    """Архангельская и Тюменская области: с округами или без них."""

    @pytest.mark.parametrize(
        ("label", "code"),
        [
            ("Архангельская область без автономного округа", "RU-ARK"),
            ("Архангельская область (без автономного округа)", "RU-ARK"),
            ("Архангельская область без Ненецкого авт.округа", "RU-ARK"),
            ("Архангельская область (с автономным округом)", "RU-ARK-AGG"),
            ("Тюменская область без автономных округов", "RU-TYU"),
            ("Arkhangelsk Oblast excluding Nenets Autonomous Okrug", "RU-ARK"),
        ],
    )
    def test_qualified_names_are_resolved(self, label: str, code: str) -> None:
        found = match(label)
        assert found.kind == EXACT
        assert found.code == code

    @pytest.mark.parametrize("label", ["Архангельская область", "Arkhangelsk Oblast", "11000000"])
    def test_bare_name_defaults_to_rosstat_total(self, label: str) -> None:
        found = match(label)
        assert found.kind == NESTED
        assert found.code == "RU-ARK-AGG"
        assert found.candidates == ("RU-ARK-AGG", "RU-ARK")

    def test_table_with_oblast_alone_settles_the_total(self) -> None:
        column = matching.match_column(
            ["Архангельская область", "Архангельская область без автономного округа", "Ненецкий АО"]
        )
        assert column.matches["Архангельская область"].kind == EXACT
        assert column.matches["Архангельская область"].code == "RU-ARK-AGG"
        assert column.nested == []

    def test_table_without_oblast_alone_asks(self) -> None:
        column = matching.match_column(["Архангельская область", "Ненецкий автономный округ"])
        assert [(item.total_code, item.alone_code) for item in column.nested] == [
            ("RU-ARK-AGG", "RU-ARK")
        ]
        assert "Архангельская область" not in column.codes()

    def test_code_column_only_confirms_name(self) -> None:
        column = matching.match_column(
            ["Белгородская область", "Курская область"],
            codes={"Белгородская область": "38000000", "Курская область": "38000000"},
        )
        assert column.codes()["Белгородская область"] == "RU-BEL"
        assert column.code_conflicts == [("Белгородская область", "38000000", "RU-KRS")]


class TestRules:
    """Правила выключаются по одному; запомненный выбор человека — сильнее правил."""

    def test_unknown_rule_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="нет правил"):
            matching.Matcher(["nothing"])

    @pytest.mark.parametrize(
        ("rule", "label"),
        [
            ("lookalikes", "Калинингpадская область"),
            ("aliases", "Башкирия"),
            ("english", "Moscow Region"),
            ("codes", "92000000"),
            ("short", "Татарстан"),
            ("composite", "Архангельская область и Ненецкий автономный округ"),
            ("fuzzy", "Красноярский карй"),
        ],
    )
    def test_rule_is_needed_for_its_case(self, rule: str, label: str) -> None:
        found = match(label)
        assert found.is_resolved
        without = matching.matcher(frozenset({rule})).match(label)
        # Без правила подпись не узнаётся или узнаётся только как исправленное написание.
        assert without.kind != found.kind or not without.is_resolved

    def test_without_nested_rule_oblast_is_silent_total(self) -> None:
        found = matching.matcher(frozenset({"nested"})).match("Архангельская область")
        assert (found.kind, found.code) == (EXACT, "RU-ARK-AGG")

    def test_remembered_choice_wins(self) -> None:
        key = matching.matcher().key("Алтай")
        found = match("Алтай", remembered={key: "RU-ALT"})
        assert found.is_resolved
        assert found.code == "RU-ALT"

    def test_remembered_outside(self) -> None:
        key = matching.matcher().key("Гос. учреждение")
        assert match("Гос. учреждение", remembered={key: OUTSIDE}).kind == OUTSIDE
