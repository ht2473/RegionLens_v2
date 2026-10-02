"""Поиск по русскому тексту без учёта регистра, «ё» и порядка слов, с исправлением опечаток."""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence

from django.db.models import CharField, Lookup, Q, TextField

# Функция приведения регистра в базе (миграция core 0001) на ICU: ``upper`` при локали
# базы ``C`` кириллицу не приводит, и штатный ``icontains`` различает регистр.
FOLD_FUNCTION = "rl_fold"

# Наибольшее число слов запроса в отборе: каждое добавляет условие с перебором таблицы.
MAX_TERMS = 6

# Наименьшая длина слова в отборе: «и», «в», «с» выдачу не сужают.
MIN_TERM_LENGTH = 2

_SPLIT_RE = re.compile(r"[^\w²³·%]+", re.UNICODE)

# Наименьшая длина исправляемого слова: «ВРП» превратилось бы в «вру».
MIN_CORRECTION_LENGTH = 4

# Порог сходства: исправляет «татарстн» и «безрабтица», но не «жильё» в «жилищный».
CORRECTION_CUTOFF = 0.75

# Знак экранирования ``LIKE`` в записи для сервера: обратная косая черта в апострофах.
LIKE_ESCAPE = "'" + chr(92) + "'"


def normalize(value: str) -> str:
    """Привести строку к виду, в котором сравниваются образец и содержимое столбца."""
    return value.lower().replace("ё", "е").strip()


def escape_like(value: str) -> str:
    """Экранировать служебные знаки образца ``LIKE``."""
    for symbol in ("\\", "%", "_"):
        value = value.replace(symbol, "\\" + symbol)
    return value


def terms(query: str) -> list[str]:
    """
    Разбить запрос на слова для отбора, отбросив короткие.

    Короткий запрос сохраняется целиком: «ВРП» и «км» вводят именно так.
    """
    words = [word for word in _SPLIT_RE.split(normalize(query)) if word]
    meaningful = [word for word in words if len(word) >= MIN_TERM_LENGTH]
    if not meaningful:
        meaningful = words
    return meaningful[:MAX_TERMS]


def correct(query: str, vocabulary: Sequence[str]) -> str:
    """
    Исправить опечатки в словах запроса по словарю слов из названий.

    Сходство считается в Python: ``pg_trgm`` при локали ``C`` не выделяет триграмм из кириллицы.
    """
    if not vocabulary:
        return query

    fixed: list[str] = []
    changed = False
    for word in terms(query):
        if len(word) < MIN_CORRECTION_LENGTH:
            fixed.append(word)
            continue

        close = difflib.get_close_matches(word, vocabulary, n=1, cutoff=CORRECTION_CUTOFF)
        if close and close[0] != word:
            fixed.append(close[0])
            changed = True
        else:
            fixed.append(word)

    return " ".join(fixed) if changed else query


def words_of(*texts: str) -> set[str]:
    """Разобрать названия на слова словаря исправлений, без коротких."""
    found: set[str] = set()
    for text in texts:
        for word in _SPLIT_RE.split(normalize(text)):
            if len(word) >= MIN_CORRECTION_LENGTH:
                found.add(word)
    return found


def search_q(query: str, *fields: str) -> Q:
    """Собрать условие отбора: каждое слово запроса нашлось хотя бы в одном из полей."""
    condition = Q()
    for term in terms(query):
        matched = Q()
        for field in fields:
            matched |= Q(**{f"{field}__ifind": term})
        condition &= matched
    return condition


class FoldedContains(Lookup):
    """Вхождение подстроки без учёта регистра и различия «е» и «ё»."""

    lookup_name = "ifind"

    def get_prep_lookup(self) -> str:
        """Привести образец к тому же виду, к какому функция базы приводит столбец."""
        return f"%{escape_like(normalize(str(self.rhs)))}%"

    def as_sql(self, compiler, connection):  # type: ignore[no-untyped-def]
        """Собрать выражение сравнения."""
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        sql = f"{FOLD_FUNCTION}({lhs}) LIKE {rhs} ESCAPE {LIKE_ESCAPE}"
        return sql, tuple(lhs_params) + tuple(rhs_params)


def register_lookups() -> None:
    """Подключить обращение ``__ifind`` ко всем текстовым полям."""
    CharField.register_lookup(FoldedContains)
    TextField.register_lookup(FoldedContains)
