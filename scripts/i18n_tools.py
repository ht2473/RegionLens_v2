"""
Извлечение переводимых строк (``extract --locale en``) и сборка ``.mo`` (``compile``)
без утилит GNU gettext; разбирает те же конструкции Python и шаблонов, что Django.
"""

from __future__ import annotations

import argparse
import ast
import re
import struct
import sys
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOCALE_DIR = BASE_DIR / "locale"

# Каталоги поиска переводимых строк и исключённые части путей.
SOURCE_DIRS = ("apps", "config", "templates")
EXCLUDED_PARTS = {".venv", "migrations", "__pycache__", "staticfiles", "node_modules"}

# --- Разбор Python ------------------------------------------------------------------------

# Имена функций перевода, вызов которых означает переводимую строку.
GETTEXT_NAMES = {"_", "gettext", "gettext_lazy", "ngettext", "ngettext_lazy"}

# --- Разбор шаблонов ----------------------------------------------------------------------

TRANSLATE_RE = re.compile(
    r"""\{%\s*(?:translate|trans)\s+(?P<quote>["'])(?P<text>.*?)(?P=quote)""",
    re.DOTALL,
)
BLOCK_RE = re.compile(
    r"\{%\s*blocktranslate(?P<options>[^%]*)%\}(?P<body>.*?)\{%\s*endblocktranslate\s*%\}",
    re.DOTALL,
)
# Строка «_("…")» в аргументах тега: include … with title=_("…"), фильтры.
TAG_RE = re.compile(r"\{%.*?%\}", re.DOTALL)
CONSTANT_RE = re.compile(r"""\b_\(\s*(?P<quote>["'])(?P<text>.*?)(?P=quote)\s*\)""", re.DOTALL)
VAR_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
PLURAL_RE = re.compile(r"\{%\s*plural\s*%\}")

# Места блоков с множественными формами, найденные при последнем обходе.
PLURAL_BLOCKS: list[str] = []

# Повторяет правило Django: при ``trimmed`` перевод строки вместе с окружающими
# пробелами заменяется одиночным пробелом.
TRIM_RE = re.compile(r"\s*\n\s*")


def iter_source_files() -> list[Path]:
    """Файлы, в которых ищутся переводимые строки."""
    files: list[Path] = []
    for directory in SOURCE_DIRS:
        root = BASE_DIR / directory
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".html", ".txt"}:
                continue
            if EXCLUDED_PARTS & set(path.parts):
                continue
            files.append(path)
    return sorted(files)


def extract_from_python(source: str, path: Path) -> list[tuple[str, str]]:
    """Извлечь переводимые строки из модуля Python разбором дерева синтаксиса."""
    found: list[tuple[str, str]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - в проекте не должно быть таких файлов
        return found

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr

        if name not in GETTEXT_NAMES or not node.args:
            continue

        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            location = path.relative_to(BASE_DIR).as_posix()
            found.append((first.value, f"{location}:{node.lineno}"))
    return found


def extract_from_template(source: str, path: Path) -> list[tuple[str, str]]:
    """Извлечь переводимые строки из шаблона Django."""
    found: list[tuple[str, str]] = []
    # Путь — с прямой косой чертой на любой системе.
    relative = path.relative_to(BASE_DIR).as_posix()

    for match in TRANSLATE_RE.finditer(source):
        line = source.count("\n", 0, match.start()) + 1
        found.append((escape_percent(match.group("text")), f"{relative}:{line}"))

    for tag in TAG_RE.finditer(source):
        for match in CONSTANT_RE.finditer(tag.group(0)):
            line = source.count("\n", 0, tag.start()) + 1
            found.append((escape_percent(match.group("text")), f"{relative}:{line}"))

    for match in BLOCK_RE.finditer(source):
        line = source.count("\n", 0, match.start()) + 1
        if PLURAL_RE.search(match.group("body")):
            # Множественные формы не собираются — о таких блоках сообщается.
            PLURAL_BLOCKS.append(f"{relative}:{line}")
            continue
        body = block_message(match.group("body"), match.group("options"))
        found.append((body, f"{relative}:{line}"))

    return found


def escape_percent(text: str) -> str:
    """
    Удвоить знак процента, как Django в идентификаторах шаблонных сообщений.

    Иначе перевод строки с «%» не находится; в Python удвоение задаёт автор строки.
    """
    return text.replace("%", "%%")


def block_message(body: str, options: str) -> str:
    """
    Построить идентификатор блока ``blocktranslate`` как ``render_token_list`` в Django.

    Сначала удваивается «%», затем подстановки становятся полями формата.
    """
    body = escape_percent(body)
    body = VAR_RE.sub(lambda match: f"%({match.group(1)})s", body)
    if "trimmed" in options:
        body = TRIM_RE.sub(" ", body.strip())
    return body


def collect_messages() -> OrderedDict[str, list[str]]:
    """Собрать все переводимые строки проекта с указанием мест их появления."""
    messages: OrderedDict[str, list[str]] = OrderedDict()

    for path in iter_source_files():
        source = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            items = extract_from_python(source, path)
        else:
            items = extract_from_template(source, path)

        for text, location in items:
            if not text.strip():
                continue
            messages.setdefault(text, []).append(location)

    return messages


# ---------------------------------------------------------------------------------------
# Чтение и запись .po
# ---------------------------------------------------------------------------------------


def parse_po(path: Path) -> dict[str, str]:
    """Прочитать существующие переводы: пары «идентификатор — перевод»."""
    if not path.exists():
        return {}

    translations: dict[str, str] = {}
    msgid: list[str] = []
    msgstr: list[str] = []
    state = ""

    def flush() -> None:
        if msgid and state:
            key = "".join(msgid)
            value = "".join(msgstr)
            if key:
                translations[key] = value

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("msgid "):
            flush()
            msgid.clear()
            msgstr.clear()
            msgid.append(unquote_po(line[len("msgid ") :]))
            state = "msgid"
        elif line.startswith("msgstr "):
            msgstr.append(unquote_po(line[len("msgstr ") :]))
            state = "msgstr"
        elif line.startswith('"') and state == "msgid":
            msgid.append(unquote_po(line))
        elif line.startswith('"') and state == "msgstr":
            msgstr.append(unquote_po(line))
        elif not line:
            flush()
            msgid.clear()
            msgstr.clear()
            state = ""

    flush()
    translations.pop("", None)
    return translations


def unquote_po(value: str) -> str:
    """Снять кавычки и экранирование со строки каталога."""
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    return (
        value.replace("\\\\", "\x00")
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\x00", "\\")
    )


def quote_po(value: str) -> str:
    """Оформить строку для записи в каталог."""
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    )
    return f'"{escaped}"'


PO_HEADER = """# Каталог сообщений интерфейса RegionLens.
#
# Файл собирается сценарием scripts/i18n_tools.py: утилиты GNU gettext на машине
# разработчика не требуются. Пустой msgstr означает, что строка показывается
# на русском языке — это допустимое поведение, а не ошибка.
#
msgid ""
msgstr ""
"Project-Id-Version: RegionLens\\n"
"Report-Msgid-Bugs-To: \\n"
"POT-Creation-Date: {created}\\n"
"Language: {language}\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"
"""


def write_po(path: Path, language: str, messages: OrderedDict[str, list[str]]) -> tuple[int, int]:
    """Записать каталог с уже сделанными переводами; вернуть «всего строк, переведено»."""
    existing = parse_po(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        PO_HEADER.format(
            created=datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M+0000"),
            language=language,
        )
    ]

    translated = 0
    for text, locations in messages.items():
        value = existing.get(text, "")
        if value:
            translated += 1

        lines.append("")
        for location in sorted(set(locations))[:5]:
            lines.append(f"#: {location}")
        lines.append(f"msgid {quote_po(text)}")
        lines.append(f"msgstr {quote_po(value)}")

    # Переводы исчезнувших строк сохраняются в конце файла с пометкой.
    obsolete = [key for key in existing if key not in messages and existing[key]]
    if obsolete:
        lines.append("")
        lines.append("# --- Устаревшие переводы: строк с такими идентификаторами в коде нет ---")
        for key in obsolete:
            lines.append("")
            lines.append(f"#~ msgid {quote_po(key)}")
            lines.append(f"#~ msgstr {quote_po(existing[key])}")

    # Окончания строк — LF: write_text в Windows без newline записал бы CRLF.
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return len(messages), translated


# ---------------------------------------------------------------------------------------
# Сборка .mo
# ---------------------------------------------------------------------------------------

# Магическое число формата, записанное в порядке байтов от младшего к старшему.
MO_MAGIC = 0x950412DE


def compile_po(po_path: Path, mo_path: Path) -> int:
    """Собрать двоичный каталог из текстового без непереведённых строк."""
    translations = {key: value for key, value in parse_po(po_path).items() if value}

    # Заголовок каталога обязателен: без него gettext не определит кодировку.
    header = (
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "Plural-Forms: nplurals=2; plural=(n != 1);\n"
    )
    entries = [("", header), *sorted(translations.items())]

    keys = b"\x00".join(key.encode("utf-8") for key, _ in entries)
    values = b"\x00".join(value.encode("utf-8") for _, value in entries)

    count = len(entries)
    key_table_offset = 7 * 4
    value_table_offset = key_table_offset + count * 8
    keys_offset = value_table_offset + count * 8
    values_offset = keys_offset + len(keys) + 1

    key_table = b""
    value_table = b""
    key_position = keys_offset
    value_position = values_offset

    for key, value in entries:
        key_bytes = key.encode("utf-8")
        value_bytes = value.encode("utf-8")
        key_table += struct.pack("<II", len(key_bytes), key_position)
        value_table += struct.pack("<II", len(value_bytes), value_position)
        key_position += len(key_bytes) + 1
        value_position += len(value_bytes) + 1

    payload = struct.pack(
        "<IiIIIii",
        MO_MAGIC,
        0,  # версия формата
        count,
        key_table_offset,
        value_table_offset,
        0,  # размер таблицы хеширования: не используется
        0,  # смещение таблицы хеширования
    )
    payload += key_table + value_table + keys + b"\x00" + values + b"\x00"

    mo_path.parent.mkdir(parents=True, exist_ok=True)
    mo_path.write_bytes(payload)
    return len(translations)


# ---------------------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------------------


def command_extract(locales: list[str]) -> None:
    """Обновить каталоги указанных языков."""
    messages = collect_messages()
    print(f"Найдено переводимых строк: {len(messages)}")
    if PLURAL_BLOCKS:
        print("Множественные формы не поддерживаются, строки пропущены:")
        for location in PLURAL_BLOCKS:
            print(f"  {location}")

    for language in locales:
        po_path = LOCALE_DIR / language / "LC_MESSAGES" / "django.po"
        total, translated = write_po(po_path, language, messages)
        share = translated / total if total else 0
        location = po_path.relative_to(BASE_DIR)
        print(f"  {language}: {translated} из {total} ({share:.0%}) — {location}")


def command_compile() -> None:
    """Собрать двоичные каталоги для всех языков."""
    if not LOCALE_DIR.exists():
        print("Каталог locale отсутствует: сначала выполните extract")
        return

    for po_path in sorted(LOCALE_DIR.rglob("django.po")):
        mo_path = po_path.with_suffix(".mo")
        count = compile_po(po_path, mo_path)
        print(f"  {po_path.parent.parent.name}: {count} строк → {mo_path.relative_to(BASE_DIR)}")


def main() -> int:
    """Разобрать аргументы и выполнить выбранное действие."""
    # Консоль Windows по умолчанию не в UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract", help="обновить .po из кода и шаблонов")
    extract.add_argument("--locale", action="append", default=None, help="код языка")

    subparsers.add_parser("compile", help="собрать .mo из .po")

    arguments = parser.parse_args()

    if arguments.command == "extract":
        command_extract(arguments.locale or ["en"])
    else:
        command_compile()
    return 0


if __name__ == "__main__":
    sys.exit(main())
