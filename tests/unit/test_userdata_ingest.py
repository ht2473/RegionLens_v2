"""Приём файлов своих данных: вид, кодировка, разделитель, архивы, вставка из буфера."""

from __future__ import annotations

import codecs
import io
import zipfile
from pathlib import Path

import pytest

from apps.userdata import html_tables, ingest, paste
from apps.userdata.ingest import IngestError

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "userdata"
SAMPLE = "Регион;2022;2023\nРеспублика Татарстан;1,5;2,5\nТверская область;3;4\n"


def _zip(path: Path, members: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> Path:
    with zipfile.ZipFile(path, "w", compression) as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return path


class TestKinds:
    """Вид файла — по первым байтам; не таблицы получают понятный отказ."""

    @pytest.mark.parametrize(
        ("head", "code"),
        [
            (b"%PDF-1.4 ...", "pdf"),
            (b"\x89PNG\r\n\x1a\n", "image"),
            (b"Rar!\x1a\x07\x00", "rar"),
            (b"7z\xbc\xaf\x27\x1c", "rar"),
            (b"\x1f\x8b\x08", "gzip"),
            (b"{\\rtf1", "word"),
            (bytes(range(256)) * 4, "binary"),
        ],
    )
    def test_refused(self, head: bytes, code: str) -> None:
        with pytest.raises(IngestError) as caught:
            ingest.sniff(head, "table.csv")
        assert caught.value.code == code

    def test_ole2_word_and_password(self) -> None:
        ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        assert ingest.sniff(ole2 + b"\x00" * 100) == ingest.XLS
        with pytest.raises(IngestError, match="паролем"):
            ingest.sniff(ole2 + "EncryptedPackage".encode("utf-16-le"))
        with pytest.raises(IngestError) as caught:
            ingest.sniff(ole2 + "WordDocument".encode("utf-16-le"))
        assert caught.value.code == "word"

    def test_text_and_markup(self) -> None:
        assert ingest.sniff(SAMPLE.encode("utf-8")) == ingest.CSV
        assert ingest.sniff(b"<html><table><tr><td>1</td></tr></table>") == ingest.HTML
        assert ingest.sniff(b"PK\x03\x04rest") == ingest.ZIP
        assert ingest.sniff(b"PAR1....") == ingest.PARQUET

    def test_docx_inside_zip_is_refused(self, tmp_path: Path) -> None:
        path = _zip(
            tmp_path / "file.docx", {"word/document.xml": b"<w/>", "[Content_Types].xml": b""}
        )
        with pytest.raises(IngestError) as caught:
            ingest.inspect(path)
        assert caught.value.code == "word"


class TestEncoding:
    """Кодировка: метка, строгий UTF-8, Windows-1251, UTF-16 без метки."""

    @pytest.mark.parametrize(
        ("data", "encoding"),
        [
            (codecs.BOM_UTF8 + SAMPLE.encode("utf-8"), "utf-8-sig"),
            (SAMPLE.encode("utf-8"), "utf-8"),
            (SAMPLE.encode("cp1251"), "cp1251"),
            (SAMPLE.encode("utf-16"), "utf-16"),
            (SAMPLE.encode("utf-16-le"), "utf-16-le"),
        ],
    )
    def test_detected(self, data: bytes, encoding: str) -> None:
        assert ingest.detect_encoding(data) == encoding

    def test_utf8_cut_inside_a_letter(self) -> None:
        data = ("Тюменская область " * 50).encode("utf-8")
        cut = data[: len(data) - 2]
        assert ingest.detect_encoding(cut) == "utf-8"
        assert ingest.complete_utf8(cut) == data[: len(data) - 3]


class TestDelimiter:
    @pytest.mark.parametrize("delimiter", [";", "\t", ",", "|"])
    def test_detected(self, delimiter: str) -> None:
        text = "\n".join(delimiter.join(["регион", "год", "значение"]) for _ in range(5)) + "\n"
        assert ingest.detect_delimiter(text) == delimiter

    def test_decimal_commas_do_not_win_over_semicolon(self) -> None:
        assert ingest.detect_delimiter(SAMPLE) == ";"


class TestFiles:
    """Таблицы файла с образцом и числом узнанных субъектов."""

    def test_long_csv_from_ebt(self) -> None:
        inspection = ingest.inspect(FIXTURES / "environment.csv")
        (table,) = inspection.tables
        assert (table.kind, table.encoding, table.delimiter) == (ingest.CSV, "utf-8", ";")
        assert table.regions == 85
        assert table.sample[0][:3] == ["indicator_section", "indicator_name", "indicator_code"]
        assert table.sample[0][table.territory_column] == "object_name"

    def test_workbook_sheet(self) -> None:
        (table,) = ingest.inspect(FIXTURES / "hiv.xlsx").tables
        assert (table.kind, table.sheet, table.regions) == (ingest.XLSX, "data", 85)
        assert table.rows_exact

    def test_cp1251_from_accounting_software(self, tmp_path: Path) -> None:
        source = (FIXTURES / "crime_wide.csv").read_bytes().decode("utf-8")
        path = tmp_path / "выгрузка.csv"
        path.write_bytes(source.encode("cp1251", errors="replace"))
        (table,) = ingest.inspect(path).tables
        assert table.encoding == "cp1251"
        assert table.regions == 85

    def test_utf16_with_tabs(self, tmp_path: Path) -> None:
        path = tmp_path / "table.txt"
        path.write_bytes(SAMPLE.replace(";", "\t").encode("utf-16"))
        (table,) = ingest.inspect(path).tables
        assert (table.encoding, table.delimiter) == ("utf-16", "\t")
        assert table.regions == 2

    def test_chosen_encoding_overrides(self, tmp_path: Path) -> None:
        path = tmp_path / "table.csv"
        path.write_bytes(SAMPLE.encode("cp1251"))
        (table,) = ingest.inspect(path, encoding="utf-8").tables
        assert table.encoding == "utf-8"
        assert "�" in table.sample[1][0]


class TestArchives:
    """Архив проверяется до распаковки; распаковывается только выбранная таблица."""

    def test_tables_documents_and_mac_leftovers(self, tmp_path: Path) -> None:
        csv_data = (FIXTURES / "environment.csv").read_bytes()
        path = _zip(
            tmp_path / "data.zip",
            {
                "data/environment.csv": csv_data,
                "data/description.pdf": b"%PDF-1.4",
                "__MACOSX/data/._environment.csv": b"\x00\x05",
                "data/.DS_Store": b"\x00",
            },
        )
        inspection = ingest.inspect(path)
        assert inspection.kind == ingest.ZIP
        assert [table.key for table in inspection.tables] == ["data/environment.csv"]
        assert inspection.tables[0].regions == 85
        assert inspection.skipped == [("data/description.pdf", "document")]

    def test_only_description(self, tmp_path: Path) -> None:
        path = _zip(tmp_path / "data.zip", {"description.pdf": b"%PDF-1.4"})
        with pytest.raises(IngestError, match="только описание"):
            ingest.inspect(path)

    def test_path_outside_archive(self, tmp_path: Path) -> None:
        path = _zip(tmp_path / "data.zip", {"../../evil.csv": SAMPLE.encode()})
        with pytest.raises(IngestError) as caught:
            ingest.inspect(path)
        assert caught.value.code == "path"

    def test_zip_bomb(self, tmp_path: Path) -> None:
        path = _zip(tmp_path / "bomb.zip", {"zeros.csv": b"0" * (8 * 1024 * 1024)})
        with pytest.raises(IngestError) as caught:
            ingest.inspect(path)
        assert caught.value.code == "ratio"

    def test_too_many_files(self, tmp_path: Path, settings: object) -> None:
        settings.USERDATA_ZIP_MAX_FILES = 3  # type: ignore[attr-defined]
        path = _zip(tmp_path / "many.zip", {f"t{index}.csv": SAMPLE.encode() for index in range(4)})
        with pytest.raises(IngestError) as caught:
            ingest.inspect(path)
        assert caught.value.code == "files"

    def test_table_too_large(self, tmp_path: Path, settings: object) -> None:
        settings.USERDATA_TABLE_MAX_BYTES = 1000  # type: ignore[attr-defined]
        path = _zip(
            tmp_path / "big.zip",
            {"big.csv": (SAMPLE * 100).encode()},
            compression=zipfile.ZIP_STORED,
        )
        with pytest.raises(IngestError) as caught:
            ingest.inspect(path)
        assert caught.value.code == "table_size"

    def test_encrypted_member(self, tmp_path: Path) -> None:
        path = _zip(tmp_path / "secret.zip", {"t.csv": SAMPLE.encode()})
        data = bytearray(path.read_bytes())
        # Флаг шифрования в локальном заголовке и в оглавлении.
        for marker in (b"PK\x03\x04", b"PK\x01\x02"):
            offset = data.find(marker)
            flag_at = offset + (6 if marker == b"PK\x03\x04" else 8)
            data[flag_at] |= 0x1
        path.write_bytes(bytes(data))
        with pytest.raises(IngestError) as caught:
            ingest.inspect(path)
        assert caught.value.code == "password"

    def test_workbook_bomb(self, tmp_path: Path) -> None:
        sheet = b"<row><c><v>0</v></c></row>" * 400_000
        path = _zip(
            tmp_path / "bomb.xlsx",
            {"xl/workbook.xml": b"<workbook/>", "xl/worksheets/sheet1.xml": sheet},
        )
        with pytest.raises(IngestError) as caught:
            ingest.inspect(path)
        assert caught.value.code == "ratio"

    def test_extract_only_chosen_member(self, tmp_path: Path) -> None:
        path = _zip(tmp_path / "data.zip", {"a.csv": SAMPLE.encode(), "b.csv": b"x;y\n1;2\n"})
        target = ingest.extract_member(path, "a.csv", tmp_path / "out" / "table.csv")
        assert target.read_bytes() == SAMPLE.encode()
        assert sorted(item.name for item in (tmp_path / "out").iterdir()) == ["table.csv"]

    def test_cp866_member_names(self, tmp_path: Path) -> None:
        # Архиватор Windows пишет имя в cp866 без флага UTF-8.
        path = _zip(tmp_path / "ru.zip", {"XXXXXXX.csv": SAMPLE.encode()})
        path.write_bytes(path.read_bytes().replace(b"XXXXXXX.csv", "таблица.csv".encode("cp866")))
        assert [table.key for table in ingest.inspect(path).tables] == ["таблица.csv"]


class TestPaste:
    """Вставка из буфера: разметка Word и текст с табуляциями, дозагрузка второй части."""

    def test_text_from_word(self) -> None:
        text = (FIXTURES / "regions_population_part1.txt").read_text(encoding="utf-8")
        rows = paste.rows_of(text)
        assert rows[0] == ["", "2010", "2015", "2020", "2022", "2023"]
        assert rows[1][0] == "Российская Федерация"

    def test_markup_wins_over_text(self) -> None:
        markup = (FIXTURES / "regions_population_part1.html").read_text(encoding="utf-8")
        rows = paste.rows_of("ignored\ttext", markup)
        assert rows[1][:2] == ["Российская Федерация", "142 849,5"]

    def test_second_part_without_repeated_header(self) -> None:
        first = paste.rows_of(
            (FIXTURES / "regions_population_part1.txt").read_text(encoding="utf-8")
        )
        second = paste.rows_of(
            (FIXTURES / "regions_population_part2.txt").read_text(encoding="utf-8")
        )
        combined = paste.append(first, [["Продолжение табл. 2.2"], *second])
        assert len(combined) == len(first) + len(second) - 1
        assert combined.count(first[0]) == 1

    def test_written_and_read_back(self, tmp_path: Path) -> None:
        rows = [["Регион", "2023"], ["Москва", "1 234,5"], ["Тверская\tобласть", ""]]
        paste.write(rows, tmp_path / paste.FILE_NAME)
        assert paste.read(tmp_path / paste.FILE_NAME) == rows


class TestMarkup:
    def test_spans_are_expanded(self) -> None:
        markup = (
            "<table><tr><td rowspan=2>Регион</td><td colspan=2>2023</td></tr>"
            "<tr><td>I кв.</td><td>II кв.</td></tr>"
            "<tr><td>Москва<br>город</td><td>1</td><td>2</td></tr></table>"
        )
        assert html_tables.tables(markup)[0] == [
            ["Регион", "2023", ""],
            ["", "I кв.", "II кв."],
            ["Москва город", "1", "2"],
        ]

    def test_scripts_are_ignored(self) -> None:
        markup = "<table><tr><td><script>alert(1)</script>Москва</td></tr></table>"
        assert html_tables.tables(markup) == [[["Москва"]]]

    def test_absurd_span_is_capped(self) -> None:
        (rows,) = html_tables.tables("<table><tr><td colspan=100000>x</td></tr></table>")
        assert len(rows[0]) == html_tables.MAX_SPAN


def test_parquet(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({"region": ["Москва", "Тверская область"], "value": [1.5, 2.5]})
    pq.write_table(table, tmp_path / "t.parquet")
    (info,) = ingest.inspect(tmp_path / "t.parquet").tables
    assert (info.kind, info.regions, info.sample[0]) == (ingest.PARQUET, 2, ["region", "value"])


def test_bytes_io_workbook_is_rewound() -> None:
    data = io.BytesIO((FIXTURES / "hiv.xlsx").read_bytes())
    ingest.check_workbook(data, ingest.XLSX)
    assert data.tell() == 0
