"""
Корпус настоящих файлов для «Своих данных»: эталонная разметка и замер точности.

    uv run python -X utf8 scripts/userdata_corpus.py labels   # дополнить эталон подписей
    uv run python -X utf8 scripts/userdata_corpus.py check    # сравнить с эталоном

Корпус и эталон лежат вне репозитория (``--corpus``, по умолчанию ``../for work/corpus``):
архивы ЕБТ — ``ebt/``, «Регионы России» — ``rosstat/``; бюллетень и ВРП — из архива сбора.
Эталон подписей — ``gold.csv``: подпись, источник, число строк, правильный ответ
(код, ``outside``, ``ask``, ``none``, ``part`` — обрывок разорванной подписи) и отметка проверки.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import csv
import io
import os
import shutil
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from python_calamine import CalamineWorkbook  # noqa: E402

from apps.sources import territories  # noqa: E402
from apps.sources.sheets import open_sheets  # noqa: E402
from apps.sources.unpack import unpack  # noqa: E402
from apps.userdata import matching  # noqa: E402

DEFAULT_CORPUS = ROOT.parent / "for work" / "corpus"
GOLD_FIELDS = ("source", "label", "rows", "expected", "alternatives", "status", "note")
SOURCES = ("ebt", "bulletin", "grp", "regions", "variants")
SOURCE_TITLES = {
    "ebt": "ЕБТ",
    "bulletin": "бюллетень",
    "grp": "ВРП",
    "regions": "«Регионы России»",
    "variants": "написания",
}
CHECKED = "проверено"
# Столбец территорий — тот, где узнаются хотя бы три территории.
MIN_TERRITORIES = 3
# Перечень таблиц файла показывается быстрее этого (план, А2).
TARGET_SECONDS = 2
DISPUTED = "спорно"
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# --- Чтение корпуса ------------------------------------------------------------------------------


def ebt_tables(corpus: Path) -> Iterator[tuple[str, list[str], Iterator[list[Any]]]]:
    """Таблицы архивов ЕБТ: имя, шапка, строки."""
    csv.field_size_limit(1 << 30)
    for archive in sorted((corpus / "ebt").glob("*.zip")):
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                name = info.filename
                if "__MACOSX" in name or not name.lower().endswith((".csv", ".xlsx")):
                    continue
                tag = f"{archive.name}/{Path(name).name}"
                if name.lower().endswith(".csv"):
                    with bundle.open(info) as raw:
                        reader = csv.reader(
                            io.TextIOWrapper(raw, encoding="utf-8", newline=""), delimiter=";"
                        )
                        header = next(reader)
                        yield tag, header, reader
                else:
                    book = CalamineWorkbook.from_filelike(io.BytesIO(bundle.read(info)))
                    for sheet in book.sheet_names:
                        rows = book.get_sheet_by_name(sheet).to_python()
                        yield f"{tag}#{sheet}", [str(cell) for cell in rows[0]], iter(rows[1:])


def rosstat_sheets(source: str) -> Iterator[tuple[str, list[list[Any]]]]:
    """Листы выпусков бюллетеня или таблицы ВРП из архива сбора."""
    folder = settings.SOURCE_ARCHIVE_DIR / (
        "rosstat_bulletin" if source == "bulletin" else "rosstat_grp"
    )
    for archive in sorted(folder.glob("*")):
        with tempfile.TemporaryDirectory() as scratch:
            for path in sorted(unpack(archive, Path(scratch)).rglob("*")):
                if path.suffix.lower() in {".xls", ".xlsx"}:
                    for sheet, rows in open_sheets(path).items():
                        yield f"{archive.name}/{path.name}#{sheet}", rows


def docx_tables(corpus: Path) -> Iterator[tuple[str, list[list[str]]]]:
    """Таблицы «Регионов России» как вставка из Word: объединённая клетка — текст и пустые."""
    with tempfile.TemporaryDirectory() as scratch:
        unpack(corpus / "rosstat" / "Region_Pokaz_2025.rar", Path(scratch))
        for path in sorted(Path(scratch).rglob("*.docx")):
            with zipfile.ZipFile(path) as bundle:
                root = ElementTree.fromstring(bundle.read("word/document.xml"))  # noqa: S314
            for number, table in enumerate(root.iter(f"{_WORD}tbl"), start=1):
                rows = []
                for row in table.iter(f"{_WORD}tr"):
                    cells: list[str] = []
                    for cell in row.findall(f"{_WORD}tc"):
                        text = " ".join(
                            "".join(node.text or "" for node in paragraph.iter(f"{_WORD}t"))
                            for paragraph in cell.iter(f"{_WORD}p")
                        ).strip()
                        span = cell.find(f"{_WORD}tcPr/{_WORD}gridSpan")
                        repeat = int(span.get(f"{_WORD}val") or 1) if span is not None else 1
                        cells.extend([text] + [""] * (repeat - 1))
                    rows.append(cells)
                yield f"{path.name}#{number}", rows


def territory_columns(rows: list[list[Any]]) -> list[int]:
    """Столбцы, где узнаются хотя бы три территории (в «Регионах России» бывает два)."""
    width = max((len(row) for row in rows), default=0)
    found = []
    for column in range(width):
        hits = sum(
            1
            for row in rows
            if len(row) > column
            and isinstance(row[column], str)
            and matching.match(row[column]).is_resolved
        )
        if hits >= MIN_TERRITORIES:
            found.append(column)
    return found


def collect_labels(corpus: Path) -> dict[tuple[str, str], int]:
    """Подписи столбцов территорий в корпусе: (источник, подпись) → число строк."""
    counts: Counter[tuple[str, str]] = Counter()
    for _tag, header, rows in ebt_tables(corpus):
        column = header.index("object_name")
        for row in rows:
            if len(row) > column and str(row[column]).strip():
                counts["ebt", str(row[column]).strip()] += 1
    print("ЕБТ прочитаны", flush=True)
    sheets: list[tuple[str, Iterator[tuple[str, list[list[Any]]]]]] = [
        ("bulletin", rosstat_sheets("bulletin")),
        ("grp", rosstat_sheets("grp")),
        ("regions", docx_tables(corpus)),
    ]
    for source, tables in sheets:
        for _tag, rows in tables:
            for column in territory_columns(rows):
                first = next(
                    index
                    for index, row in enumerate(rows)
                    if len(row) > column
                    and isinstance(row[column], str)
                    and matching.match(row[column]).is_resolved
                )
                for row in rows[first:]:
                    cell = row[column] if len(row) > column else None
                    # Числа и знаки пропуска в соседнем столбце значений — не подписи.
                    if isinstance(cell, str) and any(char.isalpha() for char in cell):
                        counts[source, cell.strip()] += 1
        print(f"{SOURCE_TITLES[source]} прочитаны", flush=True)
    return dict(counts)


# --- Эталон --------------------------------------------------------------------------------------


@dataclass(slots=True)
class GoldRow:
    """Строка эталона подписей."""

    source: str
    label: str
    rows: int
    expected: str
    alternatives: str = ""
    status: str = ""
    note: str = ""

    @property
    def is_territory(self) -> bool:
        return self.expected not in {"none", "part"}


def read_gold(path: Path) -> dict[tuple[str, str], GoldRow]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            (row["source"], row["label"]): GoldRow(
                source=row["source"],
                label=row["label"],
                rows=int(row["rows"] or 0),
                expected=row["expected"],
                alternatives=row["alternatives"],
                status=row["status"],
                note=row["note"],
            )
            for row in csv.DictReader(handle, delimiter=";")
        }


def write_gold(path: Path, gold: dict[tuple[str, str], GoldRow]) -> None:
    order = {source: index for index, source in enumerate(SOURCES)}
    rows = sorted(
        gold.values(), key=lambda item: (order[item.source], item.label.lower(), item.label)
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(GOLD_FIELDS)
    for row in rows:
        writer.writerow(
            [row.source, row.label, row.rows, row.expected, row.alternatives, row.status, row.note]
        )
    path.write_bytes(("﻿" + buffer.getvalue()).encode("utf-8"))


def propose(label: str) -> tuple[str, str, str]:
    """Предложение сопоставителя для новой строки эталона: ответ, варианты, пометка."""
    found = matching.match(label)
    if found.kind == matching.NESTED:
        return (
            found.code or "",
            " ".join(found.candidates),
            "вложенная: вопрос, если нет строки без округа",
        )
    if found.is_resolved:
        return found.code or "", "", f"правило {found.rule}"
    if found.kind == matching.OUTSIDE:
        return "outside", found.reason, ""
    if found.kind == matching.ASK:
        return "ask", " ".join(found.candidates), ""
    return "none", "", found.kind if found.kind != matching.NONE else ""


def command_labels(corpus: Path) -> None:
    """Собрать подписи корпуса и дополнить эталон; новые строки — без отметки проверки."""
    path = corpus / "gold.csv"
    gold = read_gold(path)
    counts = collect_labels(corpus)
    added = 0
    for (source, label), rows in counts.items():
        key = (source, label)
        if key in gold:
            gold[key].rows = rows
            continue
        if matching._FOOTNOTE.match(label) and not matching.match(label).is_territory:
            continue
        expected, alternatives, note = propose(label)
        gold[key] = GoldRow(source, label, rows, expected, alternatives, "", note)
        added += 1
    if not any(source == "variants" for source, _label in gold):
        for label, expected in handwritten(corpus):
            answer, alternatives, note = propose(label)
            gold["variants", label] = GoldRow(
                "variants", label, 1, expected or answer, alternatives, "", note
            )
            added += 1
    stale = [key for key, row in gold.items() if row.source != "variants" and key not in counts]
    for key in stale:
        del gold[key]
    write_gold(path, gold)
    print(f"эталон: {len(gold)} строк, добавлено {added}, убрано пропавших {len(stale)} → {path}")


# --- Сравнение ----------------------------------------------------------------------------------


Answer = Callable[[str], matching.Match]
OUTCOMES = ("ok", "nested", "asked", "wrong", "extra")


def outcome(row: GoldRow, found: matching.Match) -> str:  # noqa: PLR0911 — таблица исходов
    """
    Исход подписи: ok — верно без вопроса, nested — вложенная с верным выбором по умолчанию,
    asked — человеку зададут вопрос, wrong — ошибочное сопоставление, extra — лишний вопрос.
    """
    expected = row.expected
    if expected == "none":
        if found.is_resolved or found.kind in {matching.NESTED, matching.OUTSIDE}:
            return "wrong"
        return "extra" if found.kind == matching.ASK else "ok"
    if expected == "outside":
        if found.kind == matching.OUTSIDE:
            return "ok"
        return "wrong" if found.is_resolved or found.kind == matching.NESTED else "asked"
    if expected == "ask":
        return "wrong" if found.is_resolved or found.kind == matching.OUTSIDE else "ok"
    if found.kind == matching.NESTED:
        return "nested" if found.code == expected else "wrong"
    if found.is_resolved:
        return "ok" if found.code == expected else "wrong"
    if found.kind == matching.OUTSIDE:
        return "wrong"
    return "asked"


def evaluate(gold: list[GoldRow], answer: Answer) -> dict[str, dict[str, Counter[str]]]:
    """Исходы по источникам: по строкам таблиц и по разным подписям."""
    result: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: {"rows": Counter(), "labels": Counter()}
    )
    for row in gold:
        if row.expected == "part":
            continue
        result_key = outcome(row, answer(row.label))
        group = "territory" if row.is_territory else "other"
        for source in (row.source, "all"):
            result[source]["rows"][f"{group}:{result_key}"] += row.rows
            result[source]["labels"][f"{group}:{result_key}"] += 1
    return result


def baseline(label: str) -> matching.Match:
    """Сопоставление до 03.10.2026: точное совпадение после нормализации."""
    code = territories._lookup(territories.normalize(label), None)
    return matching.Match(matching.EXACT, code=code) if code else matching.Match(matching.NONE)


def strict(label: str) -> matching.Match:
    """Строгое сопоставление сбора с безопасными правилами."""
    code = territories.territory_code(label)
    return matching.Match(matching.EXACT, code=code) if code else matching.Match(matching.NONE)


def share(part: int, whole: int) -> str:
    return f"{100 * part / whole:.3f} %" if whole else "—"


def summary_line(name: str, counts: Counter[str]) -> str:
    territory = sum(value for key, value in counts.items() if key.startswith("territory:"))
    ok = counts["territory:ok"] + counts["territory:nested"]
    wrong = counts["territory:wrong"] + counts["other:wrong"]
    asked = counts["territory:asked"]
    extra = counts["other:extra"]
    return (
        f"{name:34} без правки {ok:>9} из {territory:<9} ({share(ok, territory):>10}); "
        f"вопросов {asked:>6} ({share(asked, territory)}), из них вложенных-по-умолчанию "
        f"{counts['territory:nested']}; ошибок {wrong}; лишних вопросов {extra}"
    )


def command_check(corpus: Path, *, verbose: bool) -> None:
    gold_map = read_gold(corpus / "gold.csv")
    gold = list(gold_map.values())
    unchecked = [row for row in gold if row.status not in {CHECKED, DISPUTED}]
    print(
        f"эталон: {len(gold)} подписей; не проверено вручную: {len(unchecked)}; "
        f"спорных: {sum(row.status == DISPUTED for row in gold)}"
    )
    full = matching.matcher()
    variants: list[tuple[str, Answer]] = [
        ("до 03.10.2026 (точное совпадение)", baseline),
        ("territory_code (сбор)", strict),
        ("сопоставитель, все правила", full.match),
    ]
    results = {name: evaluate(gold, answer) for name, answer in variants}
    for source in (*SOURCES, "all"):
        title = SOURCE_TITLES.get(source, "весь корпус")
        print(f"\n== {title}")
        for unit in ("rows", "labels"):
            print(f"  {'по строкам' if unit == 'rows' else 'по подписям'}:")
            for name, _answer in variants:
                print("    " + summary_line(name, results[name][source][unit]))
    print("\n== Написания по счёту 03.10.2026 (верно — тот же код или «нет кода», где его нет)")
    handwritten_rows = [row for row in gold if row.source == "variants"]
    for name, answer in variants:
        right = 0
        for row in handwritten_rows:
            found = answer(row.label)
            code = found.code if found.is_resolved or found.kind == matching.NESTED else None
            expected = row.expected if row.expected not in {"outside", "ask", "none"} else None
            right += code == expected
        print(f"  {name:34} {right} из {len(handwritten_rows)}")
    print("\n== Вклад правил (выключено одно; весь корпус, по подписям)")
    whole = results["сопоставитель, все правила"]["all"]["labels"]
    for rule in matching.RULES:
        counts = evaluate(gold, matching.matcher(frozenset({rule})).match)["all"]["labels"]
        delta_ok = (counts["territory:ok"] + counts["territory:nested"]) - (
            whole["territory:ok"] + whole["territory:nested"]
        )
        delta_wrong = (counts["territory:wrong"] + counts["other:wrong"]) - (
            whole["territory:wrong"] + whole["other:wrong"]
        )
        print(f"  без «{rule}»: без правки {delta_ok:+d}, ошибок {delta_wrong:+d}")
    print("\n== Ошибки и вопросы сопоставителя")
    for row in gold:
        if row.expected == "part":
            continue
        found = full.match(row.label)
        result = outcome(row, found)
        if result in {"wrong", "extra"} or (verbose and result == "asked"):
            print(
                f"  {result:6} [{row.source}] {row.label[:70]!r} ожидалось {row.expected}; "
                f"ответ {found.kind} {found.code} {found.candidates} [{found.rule}]"
            )


def command_files(corpus: Path) -> None:
    """Приём каждого файла корпуса до 25 МБ: таблицы, узнанные субъекты, время."""
    import time

    from apps.userdata import ingest, paste

    limit = settings.USERDATA_UPLOAD_MAX_BYTES
    timings: list[tuple[float, str]] = []

    def run(path: Path, name: str) -> None:
        started = time.perf_counter()
        try:
            inspection = ingest.inspect(path, name)
        except ingest.IngestError as error:
            outcome = f"отказ [{error.code}] {error}"
        else:
            best = next(table for table in inspection.tables if table.best)
            outcome = (
                f"{inspection.kind}, таблиц {len(inspection.tables)}, лучшая «{best.name}»: "
                f"субъектов {best.regions}"
            )
        elapsed = time.perf_counter() - started
        timings.append((elapsed, name))
        print(f"  {elapsed:5.2f} с  {name[:60]:60} {outcome[:110]}")

    print("== Архивы ЕБТ и «Регионы России»")
    for path in [*sorted((corpus / "ebt").glob("*.zip")), *sorted((corpus / "rosstat").glob("*"))]:
        if path.stat().st_size <= limit:
            run(path, path.name)
    print("== ВРП и таблицы бюллетеня (по одной на файл выпуска)")
    for archive in sorted(
        [
            *settings.SOURCE_ARCHIVE_DIR.glob("rosstat_grp/*"),
            *settings.SOURCE_ARCHIVE_DIR.glob("rosstat_bulletin/*"),
        ]
    ):
        with tempfile.TemporaryDirectory() as scratch:
            for path in sorted(
                item for item in unpack(archive, Path(scratch)).rglob("*") if item.is_file()
            ):
                started = time.perf_counter()
                with contextlib.suppress(ingest.IngestError):
                    ingest.inspect(path, path.name)
                timings.append((time.perf_counter() - started, f"{archive.name}/{path.name}"))
        print(f"  {archive.name}: принято")
    print("== Таблицы «Регионов России», вставленные текстом")
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / paste.FILE_NAME
        for tag, rows in docx_tables(corpus):
            text = "\n".join("\t".join(row) for row in rows)
            started = time.perf_counter()
            paste.write(paste.rows_of(text), target)
            with contextlib.suppress(ingest.IngestError):
                ingest.inspect(target, paste.FILE_NAME)
            timings.append((time.perf_counter() - started, tag))
    slowest = sorted(timings, reverse=True)[:5]
    slow = sum(elapsed > TARGET_SECONDS for elapsed, _name in timings)
    print(f"\nфайлов и вставок: {len(timings)}; дольше {TARGET_SECONDS} с: {slow}")
    print("самые долгие: " + "; ".join(f"{name} — {elapsed:.2f} с" for elapsed, name in slowest))


TABLE_FIELDS = (
    "source",
    "table",
    "form",
    "territory",
    "header",
    "values",
    "roles",
    "periods",
    "years",
    "regions",
    "unresolved",
    "questions",
    "status",
    "note",
)
# Поля, которые сравниваются с эталоном таблиц.
TABLE_CHECKED = ("form", "territory", "roles", "periods", "years")


def describe_table(recognition: Any) -> dict[str, str]:
    """Ответ распознавания строкой эталона таблиц."""
    from apps.sources.territories import region_codes
    from apps.userdata import recognize

    roles: dict[str, list[int]] = defaultdict(list)
    for column in recognition.columns:
        if column.role not in {recognize.SKIP, recognize.NOTE, recognize.TERRITORY} and (
            column.role != recognize.VALUE or recognition.form != recognize.WIDE
        ):
            roles[column.role].append(column.index)
    matches = recognition.territories.matches if recognition.territories else {}
    codes = {match.code for match in matches.values() if match.is_resolved}
    unresolved = [
        label
        for label, match in matches.items()
        if not match.is_resolved and match.kind not in {matching.OUTSIDE, matching.NESTED}
    ]
    questions = []
    if recognition.territories and recognition.territories.nested:
        questions.append(f"nested:{len(recognition.territories.nested)}")
    if recognition.needs_year:
        questions.append("year")
    if recognition.duplicates:
        questions.append(f"dup:{len(recognition.duplicates)}")
    if recognition.same_headers:
        questions.append(f"same:{len(recognition.same_headers)}")
    if recognition.text_cell_count:
        questions.append(f"text:{recognition.text_cell_count}")
    if recognition.series > recognize.MAX_SERIES:
        questions.append(f"series:{recognition.series}")
    kinds = sorted({key.split(":")[0] for key in recognition.periods.get("kinds", {})})
    years = (
        f"{recognition.periods['first']}-{recognition.periods['last']}"
        if recognition.periods
        else ""
    )
    return {
        "form": recognition.form,
        "territory": ",".join(map(str, recognition.territory_columns)),
        "header": str(len(recognition.header_rows)),
        "values": str(len(recognition.value_columns)),
        "roles": " ".join(
            f"{role}:{','.join(map(str, indexes))}" for role, indexes in sorted(roles.items())
        ),
        "periods": ",".join(kinds),
        "years": years,
        "regions": str(len(codes & region_codes())),
        "unresolved": str(len(unresolved)),
        "questions": ";".join(questions),
    }


def corpus_tables(corpus: Path) -> Iterator[tuple[str, str, Path, Any, str]]:
    """Таблицы корпуса так, как их примет приём: источник, имя, файл, таблица, имя файла."""
    from apps.userdata import ingest, paste

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        for archive in sorted((corpus / "ebt").glob("*.zip")):
            for table in ingest.inspect(archive).tables:
                target = root / f"{archive.stem}_{Path(table.member).name}"
                if table.kind in ingest.WORKBOOK_KINDS:
                    target = ingest.extract_member(archive, table.member, target)
                    yield "ebt", f"{archive.name}/{table.key}", target, table, table.member
                    continue
                ingest.extract_member(archive, table.member, target)
                yield "ebt", f"{archive.name}/{table.key}", target, table, table.member
        for source in ("grp", "bulletin"):
            for tag, path in _rosstat_files(source, root):
                try:
                    inspection = ingest.inspect(path, path.name)
                except ingest.IngestError:
                    continue
                for table in inspection.tables:
                    yield source, f"{tag}#{table.sheet}", path, table, path.name
        target = root / paste.FILE_NAME
        for tag, rows in docx_tables(corpus):
            paste.write(paste.rows_of("\n".join("\t".join(row) for row in rows)), target)
            try:
                inspection = ingest.inspect(target, paste.FILE_NAME)
            except ingest.IngestError:
                continue
            yield "regions", tag, target, inspection.tables[0], paste.FILE_NAME


def _rosstat_files(source: str, root: Path) -> Iterator[tuple[str, Path]]:
    folder = settings.SOURCE_ARCHIVE_DIR / (
        "rosstat_bulletin" if source == "bulletin" else "rosstat_grp"
    )
    for archive in sorted(folder.glob("*")):
        target = root / archive.stem
        for path in sorted(item for item in unpack(archive, target).rglob("*") if item.is_file()):
            yield f"{archive.name}/{path.relative_to(target).as_posix()}", path
        shutil.rmtree(target, ignore_errors=True)


def command_tables(corpus: Path, *, only: str = "") -> None:
    """Распознать таблицы корпуса; дополнить эталон таблиц и сравнить с ним."""
    import time

    from apps.userdata import recognize, tables

    path = corpus / "gold_tables.csv"
    gold: dict[tuple[str, str], dict[str, str]] = {}
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            gold = {
                (row["source"], row["table"]): row for row in csv.DictReader(handle, delimiter=";")
            }
    answers: dict[tuple[str, str], dict[str, str]] = {}
    slowest = (0.0, "")
    for source, tag, file_path, table, file_name in corpus_tables(corpus):
        if only and source != only:
            continue
        started = time.perf_counter()
        loaded = tables.load(file_path, table)
        recognition = recognize.recognize(loaded, table, file_name=file_name)
        slowest = max(slowest, (time.perf_counter() - started, tag))
        answers[source, tag] = {"source": source, "table": tag, **describe_table(recognition)}
    wrong: Counter[str] = Counter()
    lines = []
    for key, answer in answers.items():
        expected = gold.get(key)
        if expected is None:
            gold[key] = {**answer, "status": "", "note": ""}
            continue
        differs = [name for name in TABLE_CHECKED if expected[name] != answer[name]]
        if differs:
            wrong[key[0]] += 1
            lines.append(
                f"  [{key[0]}] {key[1]}: "
                + "; ".join(
                    f"{name}: эталон {expected[name]!r}, ответ {answer[name]!r}" for name in differs
                )
            )
    _write_table_gold(path, gold)
    by_source = Counter(source for source, _tag in answers)
    longest = f"{slowest[1]} ({slowest[0]:.1f} с)"
    print(f"таблиц: {len(answers)} {dict(by_source)}; самая долгая — {longest}")
    print("форм: " + str(Counter((key[0], answer["form"]) for key, answer in answers.items())))
    print(f"расходятся с эталоном: {sum(wrong.values())} {dict(wrong)}")
    for line in lines[:200]:
        print(line)


def _write_table_gold(path: Path, gold: dict[tuple[str, str], dict[str, str]]) -> None:
    order = {source: index for index, source in enumerate(SOURCES)}
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(TABLE_FIELDS)
    for key in sorted(gold, key=lambda item: (order.get(item[0], 9), item[1])):
        writer.writerow([gold[key].get(name, "") for name in TABLE_FIELDS])
    path.write_bytes(("﻿" + buffer.getvalue()).encode("utf-8"))


def handwritten(corpus: Path) -> list[tuple[str, str | None]]:
    """132 написания «от руки» из пробного сценария 03.10.2026; ``None`` — нет одного кода."""
    source = (corpus / "scripts" / "variants.py").read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "VARIANTS":
            return list(ast.literal_eval(node.value)) if node.value else []
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    parser.add_argument("command", choices=("labels", "check", "files", "tables"))
    parser.add_argument(
        "--only", default="", help="только один источник: ebt, bulletin, grp, regions"
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--verbose", action="store_true", help="показывать и вопросы")
    arguments = parser.parse_args()
    if arguments.command == "labels":
        command_labels(arguments.corpus)
    elif arguments.command == "files":
        command_files(arguments.corpus)
    elif arguments.command == "tables":
        command_tables(arguments.corpus, only=arguments.only)
    else:
        command_check(arguments.corpus, verbose=arguments.verbose)


if __name__ == "__main__":
    main()
