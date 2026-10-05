"""
Формулы своих данных: «[ДТП] / [Автомобили] * 1000», «AGO([X], 1)», «RANK([X])».

Свой разборщик строит дерево, ссылки на ряды (этой таблицы, других своих таблиц, официальные)
заменяются ключами, дерево переводится в выражение DuckDB с параметрами и считается
в соединении в памяти без доступа к файлам и с запертыми настройками. Текста пользователя
в запросе нет: имена столбцов и функций — из перечня модуля, числа — параметрами.

В рецепте формула хранится с ключами рядов: «{u:abc:def} / {Y477110461:00}». Переименование
показателя формулу не ломает; человеку она показывается с текущими названиями.
"""

from __future__ import annotations

import math
import re
import secrets
import string
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import duckdb
import pandas as pd
from django.utils.translation import gettext as _

# Пределы формулы.
MAX_LENGTH = 500
MAX_DEPTH = 20
MAX_REFERENCES = 12
MAX_LAG = 50
MAX_DIGITS = 10
MAX_ARGUMENTS = 8
MAX_FORMULAS = 50
# Числа длиннее этого — опечатка, а не значение.
MAX_NUMBER_LENGTH = 20
CODE_ALPHABET = string.ascii_lowercase + string.digits
CODE_LENGTH = 8
# Память соединения, в котором считается формула.
MEMORY = "256MB"

# Функции: имя → (наименьшее и наибольшее число аргументов). Русские имена — как в Excel.
FUNCTIONS: dict[str, tuple[int, int]] = {
    "ABS": (1, 1),
    "ROUND": (1, 2),
    "LN": (1, 1),
    "LOG10": (1, 1),
    "SQRT": (1, 1),
    "EXP": (1, 1),
    "MIN": (2, MAX_ARGUMENTS),
    "MAX": (2, MAX_ARGUMENTS),
    "AGO": (2, 2),
    "RANK": (1, 1),
}
ALIASES = {
    "ОКРУГЛ": "ROUND",
    "КОРЕНЬ": "SQRT",
    "МИН": "MIN",
    "МАКС": "MAX",
    "РАНГ": "RANK",
    "НАЗАД": "AGO",
}
OPERATORS = "+-*/^"

_NAME = re.compile(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?")
_KEY = re.compile(r"^[A-Za-z0-9_:.\-]{1,80}$")
_DASHES = re.compile(r"[‐-―−]")


class FormulaError(ValueError):
    """Формулу не разобрать или не посчитать: текст для человека."""


# --- Дерево -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Number:
    value: float


@dataclass(slots=True)
class Reference:
    """Ссылка на ряд: подпись в квадратных скобках или ключ в фигурных."""

    text: str
    position: int
    is_key: bool = False
    key: str = ""


@dataclass(frozen=True, slots=True)
class Unary:
    operator: str
    operand: Node


@dataclass(frozen=True, slots=True)
class Binary:
    operator: str
    left: Node
    right: Node


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    arguments: tuple[Node, ...]
    position: int


Node = Number | Reference | Unary | Binary | Call


@dataclass(frozen=True, slots=True)
class Token:
    kind: str  # number, reference, key, name, operator, open, close, separator, end
    text: str
    position: int


def tokenize(text: str) -> list[Token]:
    """Разбить формулу на знаки; непонятный знак — ошибка с местом."""
    if len(text) > MAX_LENGTH:
        raise FormulaError(_("Формула длиннее %(limit)s знаков.") % {"limit": MAX_LENGTH})
    tokens: list[Token] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "[{":
            token, index = _bracket(text, index)
            tokens.append(token)
            continue
        number = _NUMBER.match(text, index)
        if number:
            if len(number.group()) > MAX_NUMBER_LENGTH:
                raise FormulaError(
                    _("Слишком длинное число у знака %(position)s.") % {"position": index + 1}
                )
            tokens.append(Token("number", number.group(), index))
            index = number.end()
            continue
        name = _NAME.match(text, index)
        if name:
            tokens.append(Token("name", name.group(), index))
            index = name.end()
            continue
        if char in OPERATORS:
            tokens.append(Token("operator", char, index))
        elif char == "(":
            tokens.append(Token("open", char, index))
        elif char == ")":
            tokens.append(Token("close", char, index))
        elif char in ",;":
            tokens.append(Token("separator", char, index))
        else:
            raise FormulaError(
                _("Непонятный знак «%(char)s» у знака %(position)s.")
                % {"char": char, "position": index + 1}
            )
        index += 1
    tokens.append(Token("end", "", len(text)))
    return tokens


def _bracket(text: str, index: int) -> tuple[Token, int]:
    """Ссылка в скобках: подпись в квадратных, ключ — в фигурных."""
    closing = "]" if text[index] == "[" else "}"
    end = text.find(closing, index + 1)
    if end < 0:
        raise FormulaError(
            _("Нет закрывающей скобки «%(bracket)s» после знака %(position)s.")
            % {"bracket": closing, "position": index + 1}
        )
    inner = " ".join(text[index + 1 : end].split())
    if not inner:
        raise FormulaError(
            _("Пустая ссылка на показатель у знака %(position)s.") % {"position": index + 1}
        )
    return Token("reference" if closing == "]" else "key", inner, index), end + 1


class _Parser:
    """Разбор по приоритету: сложение, умножение, знак, степень, скобки и функции."""

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0
        self.depth = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaError(
                _("Слишком глубокая вложенность: больше %(limit)s уровней.") % {"limit": MAX_DEPTH}
            )

    def parse(self) -> Node:
        if self.current.kind == "end":
            raise FormulaError(_("Формула пустая."))
        node = self.expression()
        if self.current.kind != "end":
            raise self.unexpected()
        return node

    def unexpected(self) -> FormulaError:
        token = self.current
        if token.kind == "end":
            return FormulaError(_("Формула обрывается: не хватает числа, показателя или скобки."))
        return FormulaError(
            _("Неожиданное «%(text)s» у знака %(position)s.")
            % {"text": token.text, "position": token.position + 1}
        )

    def expression(self) -> Node:
        self.enter()
        node = self.term()
        while self.current.kind == "operator" and self.current.text in "+-":
            operator = self.advance().text
            node = Binary(operator, node, self.term())
        self.depth -= 1
        return node

    def term(self) -> Node:
        node = self.unary()
        while self.current.kind == "operator" and self.current.text in "*/":
            operator = self.advance().text
            node = Binary(operator, node, self.unary())
        return node

    def unary(self) -> Node:
        if self.current.kind == "operator" and self.current.text in "+-":
            operator = self.advance().text
            self.enter()
            operand = self.unary()
            self.depth -= 1
            return operand if operator == "+" else Unary("-", operand)
        return self.power()

    def power(self) -> Node:
        node = self.primary()
        if self.current.kind == "operator" and self.current.text == "^":
            self.advance()
            self.enter()
            exponent = self.unary()
            self.depth -= 1
            return Binary("^", node, exponent)
        return node

    def primary(self) -> Node:
        token = self.current
        if token.kind == "number":
            self.advance()
            return Number(float(token.text.replace(",", ".")))
        if token.kind in {"reference", "key"}:
            self.advance()
            is_key = token.kind == "key"
            return Reference(
                token.text, token.position, is_key=is_key, key=token.text if is_key else ""
            )
        if token.kind == "open":
            self.advance()
            node = self.expression()
            if self.current.kind != "close":
                raise FormulaError(
                    _("Не закрыта скобка, открытая у знака %(position)s.")
                    % {"position": token.position + 1}
                )
            self.advance()
            return node
        if token.kind == "name":
            return self.call()
        raise self.unexpected()

    def call(self) -> Node:
        token = self.advance()
        name = ALIASES.get(token.text.upper(), token.text.upper())
        if name not in FUNCTIONS:
            raise FormulaError(
                _(
                    "Нет функции «%(name)s». Можно: %(names)s. Показатель пишется "
                    "в квадратных скобках: [ДТП]."
                )
                % {"name": token.text, "names": ", ".join(FUNCTIONS)}
            )
        if self.current.kind != "open":
            raise FormulaError(
                _("После «%(name)s» нужна скобка с аргументами.") % {"name": token.text}
            )
        self.advance()
        self.enter()
        arguments: list[Node] = []
        if self.current.kind != "close":
            arguments.append(self.expression())
            while self.current.kind == "separator":
                self.advance()
                arguments.append(self.expression())
        if self.current.kind != "close":
            raise self.unexpected()
        self.advance()
        self.depth -= 1
        low, high = FUNCTIONS[name]
        if not low <= len(arguments) <= high:
            raise FormulaError(
                _("У %(name)s аргументов должно быть от %(low)s до %(high)s, а их %(count)s.")
                % {"name": name, "low": low, "high": high, "count": len(arguments)}
                if low != high
                else _("У %(name)s аргументов должно быть %(low)s, а их %(count)s.")
                % {"name": name, "low": low, "count": len(arguments)}
            )
        if name == "AGO":
            _integer(arguments[1], name, 1, MAX_LAG)
        if name == "ROUND" and len(arguments) > 1:
            _integer(arguments[1], name, 0, MAX_DIGITS)
        return Call(name, tuple(arguments), token.position)


def _integer(node: Node, name: str, low: int, high: int) -> int:
    """Целое число в границах — второй аргумент AGO и ROUND."""
    if (
        not isinstance(node, Number)
        or node.value != int(node.value)
        or not low <= node.value <= high
    ):
        raise FormulaError(
            _("Второй аргумент %(name)s — целое число от %(low)s до %(high)s.")
            % {"name": name, "low": low, "high": high}
        )
    return int(node.value)


def parse(text: str) -> Node:
    """Дерево формулы; ошибка разбора — ``FormulaError`` с понятным текстом."""
    return _Parser(tokenize(text)).parse()


def references(node: Node) -> list[Reference]:
    """Ссылки на ряды в порядке появления."""
    found: list[Reference] = []

    def walk(item: Node) -> None:
        if isinstance(item, Reference):
            found.append(item)
        elif isinstance(item, Unary):
            walk(item.operand)
        elif isinstance(item, Binary):
            walk(item.left)
            walk(item.right)
        elif isinstance(item, Call):
            for argument in item.arguments:
                walk(argument)

    walk(node)
    return found


# --- Сопоставление ссылок -----------------------------------------------------------------------


def normalized(text: str) -> str:
    """Подпись для сравнения: регистр, «ё», тире и пробелы не важны."""
    text = _DASHES.sub("-", str(text)).replace("ё", "е").replace("Ё", "Е")
    return " ".join(text.casefold().split())


@dataclass(slots=True)
class Candidate:
    """Ряд, на который может указывать ссылка."""

    key: str
    title: str
    source: str  # this, table, official
    table: str = ""


@dataclass(slots=True)
class Directory:
    """Ряды, доступные формуле: по подписи и по ключу."""

    by_label: dict[str, list[Candidate]] = field(default_factory=dict)
    by_key: dict[str, Candidate] = field(default_factory=dict)
    tables: set[str] = field(default_factory=set)

    def add(self, candidate: Candidate, labels: Iterable[str]) -> None:
        self.by_key.setdefault(candidate.key, candidate)
        for label in labels:
            if not label:
                continue
            bucket = self.by_label.setdefault(normalized(label), [])
            if all(item.key != candidate.key for item in bucket):
                bucket.append(candidate)
        if candidate.table:
            self.tables.add(normalized(candidate.table))

    def resolve(self, reference: Reference) -> Candidate:
        """Ряд по ссылке; нет такого или подходит несколько — ошибка с подсказкой."""
        if reference.is_key:
            found = self.by_key.get(reference.text)
            if found is None or not _KEY.match(reference.text):
                raise FormulaError(
                    _("Нет показателя с ключом «%(key)s».") % {"key": reference.text}
                )
            return found
        text = normalized(reference.text)
        matches = self.by_label.get(text, [])
        table, _sep, rest = reference.text.partition(":")
        if not matches and rest and normalized(table) in self.tables:
            matches = [
                item
                for item in self.by_label.get(normalized(rest), [])
                if normalized(item.table) == normalized(table)
            ]
        if not matches:
            raise FormulaError(_("Нет показателя «%(name)s».") % {"name": reference.text})
        own = [item for item in matches if item.source == "this"]
        if len(own) == 1:
            return own[0]
        if len(matches) > 1:
            places = sorted({item.table or _("официальные ряды") for item in matches})
            raise FormulaError(
                _(
                    "«%(name)s» подходит к нескольким рядам (%(places)s). Уточните название "
                    "или таблицу: [Таблица: показатель]."
                )
                % {"name": reference.text, "places": "; ".join(places)}
            )
        return matches[0]


def canonical(text: str, directory: Directory) -> tuple[str, list[str]]:
    """
    Формула с ключами вместо подписей и перечень ключей; ошибка — подпись не найдена или
    ссылок слишком много.
    """
    tree = parse(text)
    found = references(tree)
    keys: list[str] = []
    replacements: list[tuple[int, int, str]] = []
    tokens = [token for token in tokenize(text) if token.kind in {"reference", "key"}]
    for reference, token in zip(found, sorted(tokens, key=lambda item: item.position), strict=True):
        candidate = directory.resolve(reference)
        reference.key = candidate.key
        if candidate.key not in keys:
            keys.append(candidate.key)
        closing = "]" if token.kind == "reference" else "}"
        end = text.index(closing, token.position + 1) + 1
        replacements.append((token.position, end, "{" + candidate.key + "}"))
    if not keys:
        raise FormulaError(_("В формуле нет ни одного показателя: их пишут в квадратных скобках."))
    if len(keys) > MAX_REFERENCES:
        raise FormulaError(
            _("В формуле больше %(limit)s разных показателей.") % {"limit": MAX_REFERENCES}
        )
    result = text
    for start, end, key in reversed(replacements):
        result = result[:start] + key + result[end:]
    return " ".join(result.split()), keys


def display(expression: str, titles: Mapping[str, str]) -> str:
    """Формула с ключами — с текущими названиями рядов в квадратных скобках."""

    def label(match: re.Match[str]) -> str:
        key = match.group(1)
        title = titles.get(key)
        return f"[{title}]" if title else "{" + key + "}"

    return re.sub(r"\{([^{}]+)\}", label, expression)


def keys_of(expression: str) -> list[str]:
    """Ключи рядов формулы, записанной с ключами."""
    return list(dict.fromkeys(re.findall(r"\{([^{}]+)\}", expression)))


def new_code(existing: Iterable[str]) -> str:
    """Код ряда формулы: постоянный, не совпадающий с кодами таблицы."""
    taken = set(existing)
    while True:
        code = "f" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if code not in taken:
            return code


# --- Вычисление ---------------------------------------------------------------------------------


@dataclass(slots=True)
class Outcome:
    """Итог формулы: значения и что о них сказать человеку."""

    rows: list[tuple[str, int, float]]
    zero_division: int = 0
    regions: int = 0
    years: tuple[int, int] | None = None

    @property
    def warnings(self) -> list[str]:
        found = []
        if self.zero_division:
            found.append(
                _("Субъектов с делением на ноль: %(count)s — их значения пусты.")
                % {"count": self.zero_division}
            )
        return found


class _Compiler:
    """Дерево → выражение DuckDB: окна (AGO, RANK) и знаменатели — отдельными столбцами."""

    def __init__(self, columns: Mapping[str, str]) -> None:
        self.columns = columns
        self.stages: list[tuple[str, list[float]]] = []
        self.zero_columns: list[str] = []
        self.count = 0

    def column(self) -> str:
        self.count += 1
        return f"c{self.count}"

    def stage(self, sql: str, parameters: list[float]) -> str:
        name = self.column()
        self.stages.append((f"{sql} AS {name}", parameters))
        return name

    def compile(self, node: Node) -> tuple[str, list[float]]:
        if isinstance(node, Number):
            return "CAST(? AS DOUBLE)", [node.value]
        if isinstance(node, Reference):
            return self.columns[node.key], []
        if isinstance(node, Unary):
            sql, parameters = self.compile(node.operand)
            return f"(-({sql}))", parameters
        if isinstance(node, Binary):
            return self.binary(node)
        return self.call(node)

    def binary(self, node: Binary) -> tuple[str, list[float]]:
        left, left_parameters = self.compile(node.left)
        right, right_parameters = self.compile(node.right)
        if node.operator == "/":
            denominator = self.stage(f"({right})", right_parameters)
            self.zero_columns.append(denominator)
            return (
                f"(CASE WHEN {denominator} = 0 THEN NULL ELSE ({left}) / {denominator} END)",
                left_parameters,
            )
        if node.operator == "^":
            return f"power({left}, {right})", left_parameters + right_parameters
        return f"(({left}) {node.operator} ({right}))", left_parameters + right_parameters

    def call(self, node: Call) -> tuple[str, list[float]]:  # noqa: PLR0911 — по ветви на функцию
        name = node.name
        if name == "AGO":
            argument, parameters = self.compile(node.arguments[0])
            lag = _integer(node.arguments[1], name, 1, MAX_LAG)
            return (
                self.stage(
                    f"lag({argument}, {lag}) OVER (PARTITION BY territory_code ORDER BY year)",
                    parameters,
                ),
                [],
            )
        if name == "RANK":
            argument, parameters = self.compile(node.arguments[0])
            present = f"(is_subject AND ({argument}) IS NOT NULL)"
            sql = (
                f"CASE WHEN {present} THEN rank() OVER "
                f"(PARTITION BY year, {present} ORDER BY ({argument}) DESC) END"
            )
            return self.stage(sql, parameters * 3), []
        compiled = [self.compile(argument) for argument in node.arguments]
        first, parameters = compiled[0]
        if name == "ABS":
            return f"abs({first})", parameters
        if name == "ROUND":
            digits = _integer(node.arguments[1], name, 0, MAX_DIGITS) if len(compiled) > 1 else 0
            return f"round({first}, {digits})", parameters
        if name == "LN":
            return f"(CASE WHEN ({first}) > 0 THEN ln({first}) END)", parameters * 2
        if name == "LOG10":
            return f"(CASE WHEN ({first}) > 0 THEN log10({first}) END)", parameters * 2
        if name == "SQRT":
            return f"(CASE WHEN ({first}) >= 0 THEN sqrt({first}) END)", parameters * 2
        if name == "EXP":
            return f"exp({first})", parameters
        # MIN и MAX: пропуск в любом аргументе — пропуск результата.
        texts = [sql for sql, _parameters in compiled]
        flat = [value for _sql, values in compiled for value in values]
        missing = " OR ".join(f"({sql}) IS NULL" for sql in texts)
        function = "least" if name == "MIN" else "greatest"
        return (
            f"(CASE WHEN {missing} THEN NULL ELSE {function}({', '.join(texts)}) END)",
            flat + flat,
        )


def evaluate(
    tree: Node,
    inputs: Mapping[str, Sequence[tuple[str, int, float | None]]],
    territories: Mapping[str, bool],
) -> Outcome:
    """
    Посчитать формулу по значениям рядов (ключ → территория, год, значение) на всех
    территориях ``territories`` (код → субъект ли) и годах, где есть значения.
    """
    keys = list(dict.fromkeys(reference.key for reference in references(tree)))
    columns = {key: f"r{index}" for index, key in enumerate(keys)}
    years = sorted({int(year) for rows in inputs.values() for _code, year, _value in rows})
    if not years:
        return Outcome(rows=[])
    grid = _grid(keys, columns, inputs, territories, range(years[0], years[-1] + 1))
    compiler = _Compiler(columns)
    final, final_parameters = compiler.compile(tree)

    parts = ["s0 AS (SELECT * FROM grid)"]
    parameters: list[float] = []
    for number, (sql, stage_parameters) in enumerate(compiler.stages, start=1):
        # В тексте — только имена столбцов и функций из модуля; числа — параметрами.
        parts.append(f"s{number} AS (SELECT *, {sql} FROM s{number - 1})")  # noqa: S608
        parameters += stage_parameters
    zero = (
        " OR ".join(f"{name} = 0" for name in compiler.zero_columns)
        if compiler.zero_columns
        else "false"
    )
    # Имена столбцов и функций — из модуля, числа — параметрами.
    query = (
        f"WITH {', '.join(parts)} "  # noqa: S608
        f"SELECT territory_code, year, is_subject, ({final}) AS value, ({zero}) AS zero "
        f"FROM s{len(compiler.stages)} ORDER BY territory_code, year"
    )
    parameters += final_parameters
    connection = _sandbox()
    try:
        connection.register("grid", grid)
        result = connection.execute(query, parameters).fetchall()
    except duckdb.Error as error:
        raise FormulaError(_("Формулу не удалось посчитать по этим данным.")) from error
    finally:
        connection.close()
    rows: list[tuple[str, int, float]] = []
    zero_subjects: set[str] = set()
    subjects: set[str] = set()
    for code, year, is_subject, value, zero_hit in result:
        if zero_hit and is_subject:
            zero_subjects.add(code)
        if value is None or not _finite(value):
            continue
        rows.append((str(code), int(year), float(value)))
        if is_subject:
            subjects.add(code)
    found_years = [year for _code, year, _value in rows]
    return Outcome(
        rows=rows,
        zero_division=len(zero_subjects),
        regions=len(subjects),
        years=(min(found_years), max(found_years)) if found_years else None,
    )


def _finite(value: float) -> bool:
    return math.isfinite(value)


def _grid(
    keys: list[str],
    columns: Mapping[str, str],
    inputs: Mapping[str, Sequence[tuple[str, int, float | None]]],
    territories: Mapping[str, bool],
    years: range,
) -> pd.DataFrame:
    """Все территории × все годы подряд: сдвиг AGO на n строк — это n лет."""
    codes = sorted(territories)
    frame = pd.DataFrame(
        {
            "territory_code": [code for code in codes for _year in years],
            "year": [year for _code in codes for year in years],
            "is_subject": [territories[code] for code in codes for _year in years],
        }
    )
    position = {
        (code, year): index
        for index, (code, year) in enumerate(
            zip(frame["territory_code"], frame["year"], strict=True)
        )
    }
    for key in keys:
        values: list[float | None] = [None] * len(frame)
        for code, year, value in inputs.get(key, ()):
            index = position.get((code, int(year)))
            if index is not None and value is not None:
                values[index] = float(value)
        frame[columns[key]] = pd.Series(values, dtype="float64")
    return frame


def _sandbox() -> duckdb.DuckDBPyConnection:
    """Соединение в памяти: без доступа к файлам, один поток, настройки заперты."""
    connection = duckdb.connect()
    connection.execute(f"SET memory_limit = '{MEMORY}'")
    connection.execute("SET threads = 1")
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    return connection
