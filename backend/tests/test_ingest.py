from pathlib import Path

import polars as pl
import pytest

from app.ingest.readers import SRC_ROW, UnsupportedFile, detect_kind, land, list_sheets
from app.ingest.sniff import sniff_text

SAMPLES = Path(__file__).resolve().parents[2] / "samples"

# (fixture, kind, rows, columns, first _src_row) — the first _src_row is the physical line or
# sheet row the first data record occupies in the original file.
FIXTURES = [
    ("orders_clean.csv", "csv", 30, 12, 2),
    ("orders_messy.csv", "csv", 30, 12, 6),
    ("orders_euro.xlsx", "excel", 30, 12, 3),
    ("orders_nested.jsonl", "json", 30, 14, 1),
]


@pytest.mark.parametrize("name,kind,rows,columns,first_row", FIXTURES)
def test_fixture_lands_with_expected_shape(name, kind, rows, columns, first_row):
    landed = land(SAMPLES / name)
    frame = landed.frame
    assert landed.kind == kind
    assert frame.height == rows
    assert frame.width - 1 == columns
    assert frame[SRC_ROW][0] == first_row


@pytest.mark.parametrize("name,kind,rows,columns,first_row", FIXTURES)
def test_every_data_column_lands_as_utf8(name, kind, rows, columns, first_row):
    frame = land(SAMPLES / name).frame
    for column, dtype in zip(frame.columns, frame.dtypes, strict=True):
        if column == SRC_ROW:
            assert dtype == pl.Int64
        else:
            assert dtype == pl.Utf8, f"{name}:{column} landed as {dtype}, not Utf8"


@pytest.mark.parametrize("name,kind,rows,columns,first_row", FIXTURES)
def test_src_row_is_contiguous(name, kind, rows, columns, first_row):
    values = land(SAMPLES / name).frame[SRC_ROW].to_list()
    assert values == list(range(first_row, first_row + rows))


def test_messy_csv_sniffs_encoding_delimiter_and_preamble():
    sniff = sniff_text(SAMPLES / "orders_messy.csv")
    assert sniff.encoding == "cp1252"
    assert sniff.delimiter == ";"
    assert sniff.header_row == 4
    assert len(sniff.preamble) == 4
    assert sniff.column_count == 12


def test_clean_csv_needs_no_preamble_skipping():
    sniff = sniff_text(SAMPLES / "orders_clean.csv")
    assert sniff.encoding == "utf-8"
    assert sniff.delimiter == ","
    assert sniff.header_row == 0
    assert sniff.preamble == []


def test_cp1252_high_bytes_survive_the_round_trip():
    frame = land(SAMPLES / "orders_messy.csv").frame
    assert "Café table leg" in frame["Description"].to_list()


def test_all_string_landing_preserves_lossy_values():
    """European decimals and ambiguous dates must arrive verbatim; inferring at read time
    would destroy the evidence the profiler needs."""
    frame = land(SAMPLES / "orders_euro.xlsx").frame
    assert "1.142,00" in frame["Betrag_Einzelpreis"].to_list()
    assert all("/" in v for v in frame["Termine_Bestelldatum"].to_list())


def test_excel_picks_the_data_sheet_over_cover_and_notes():
    landed = land(SAMPLES / "orders_euro.xlsx")
    assert list_sheets(SAMPLES / "orders_euro.xlsx") == ["Deckblatt", "Bestellungen", "Hinweise"]
    assert landed.sheet == "Bestellungen"


def test_excel_merged_header_flattens_to_parent_child():
    frame = land(SAMPLES / "orders_euro.xlsx").frame
    assert "Bestellung_Bestellnr" in frame.columns
    assert "Betrag_Einzelpreis" in frame.columns
    assert "Termine_Status" in frame.columns


def test_nested_json_flattens_to_dotted_paths_and_keeps_key_drift():
    frame = land(SAMPLES / "orders_nested.jsonl").frame
    assert "customer.id" in frame.columns
    assert "price.amount" in frame.columns
    # The export spells quantity two ways; both must survive as separate columns.
    assert {"quantity", "qty"} <= set(frame.columns)


def test_unsupported_extension_is_rejected(tmp_path):
    path = tmp_path / "notes.docx"
    path.write_bytes(b"")
    with pytest.raises(UnsupportedFile):
        detect_kind(path)
