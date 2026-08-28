import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from app.ingest.sniff import TextSniff, sniff_text

SRC_ROW = "_src_row"

TEXT_SUFFIXES = {".csv", ".tsv", ".txt"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}


class UnsupportedFile(ValueError):
    pass


@dataclass
class LandedFrame:
    frame: pl.DataFrame
    kind: str
    sheet: str | None
    sniff: dict[str, Any]


def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return "csv"
    if suffix in EXCEL_SUFFIXES:
        return "excel"
    if suffix in JSON_SUFFIXES:
        return "json"
    raise UnsupportedFile(f"Unsupported file type: {path.suffix or path.name}")


def list_sheets(path: Path) -> list[str]:
    import fastexcel

    return fastexcel.read_excel(str(path)).sheet_names


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for raw in names:
        name = (raw or "").strip() or "column"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


def _as_utf8(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pl.all().cast(pl.Utf8, strict=False))


def _with_src_row(frame: pl.DataFrame, first_data_row: int) -> pl.DataFrame:
    return frame.with_row_index(SRC_ROW, offset=first_data_row).with_columns(
        pl.col(SRC_ROW).cast(pl.Int64)
    )


def read_csv(path: Path, sniff: TextSniff | None = None) -> LandedFrame:
    detected = sniff or sniff_text(path)
    text = path.read_bytes().decode(detected.encoding, errors="replace")
    body = "\n".join(text.splitlines()[detected.header_row :])
    frame = pl.read_csv(
        body.encode("utf-8"),
        separator=detected.delimiter,
        has_header=True,
        infer_schema_length=0,
        truncate_ragged_lines=True,
        quote_char='"',
    )
    frame = _as_utf8(frame)
    frame.columns = _dedupe(list(frame.columns))
    return LandedFrame(
        frame=_with_src_row(frame, detected.header_row + 2),
        kind="csv",
        sheet=None,
        sniff={
            "encoding": detected.encoding,
            "delimiter": detected.delimiter,
            "header_row": detected.header_row,
            "preamble": detected.preamble,
        },
    )


def _flatten_header(rows: list[list[Any]]) -> tuple[list[str], int]:
    """Collapse a merged/two-row Excel header into parent_child names.

    xlsxwriter-style merged cells arrive with the label only in the first cell of the span,
    so the group name is carried forward across the blanks that follow it.
    """
    header_rows: list[list[str]] = []
    for row in rows[:3]:
        cells = ["" if c is None else str(c).strip() for c in row]
        if not any(cells):
            continue
        header_rows.append(cells)
        if len(header_rows) == 2:
            break

    if not header_rows:
        return [], 0
    if len(header_rows) == 1:
        return header_rows[0], 1

    parents, children = header_rows[0], header_rows[1]
    if sum(1 for c in children if c) < sum(1 for c in parents if c):
        return parents, 1

    names: list[str] = []
    carried = ""
    for index, child in enumerate(children):
        parent = parents[index] if index < len(parents) else ""
        if parent:
            carried = parent
        if not child:
            names.append(carried or f"column_{index}")
        elif carried and carried.lower() != child.lower():
            names.append(f"{carried}_{child}")
        else:
            names.append(child)
    return names, 2


def read_excel(path: Path, sheet: str | None = None) -> LandedFrame:
    sheets = list_sheets(path)
    if not sheets:
        raise UnsupportedFile(f"No sheets found in {path.name}")
    target = sheet or _pick_sheet(path, sheets)

    raw = pl.read_excel(
        path,
        sheet_name=target,
        engine="calamine",
        has_header=False,
        read_options={"dtypes": "string"},
    )
    rows = raw.rows()
    names, consumed = _flatten_header(rows)
    if not names:
        raise UnsupportedFile(f"Sheet {target!r} in {path.name} is empty")

    skipped = 0
    for row in rows:
        if any(c is not None and str(c).strip() for c in row):
            break
        skipped += 1

    body = rows[skipped + consumed :]
    names = _dedupe(names)
    columns: dict[str, list[str | None]] = {name: [] for name in names}
    for row in body:
        if not any(c is not None and str(c).strip() for c in row):
            continue
        for index, name in enumerate(names):
            cell = row[index] if index < len(row) else None
            columns[name].append(None if cell is None else str(cell).strip())
    frame = pl.DataFrame(columns, schema={name: pl.Utf8 for name in names})

    return LandedFrame(
        frame=_with_src_row(frame, skipped + consumed + 1),
        kind="excel",
        sheet=target,
        sniff={"sheet": target, "sheets": sheets, "header_rows": consumed},
    )


def _pick_sheet(path: Path, sheets: list[str]) -> str:
    """Choose the sheet with the most tabular content; cover/notes sheets are narrow."""
    best, best_score = sheets[0], -1
    for name in sheets:
        try:
            probe = pl.read_excel(
                path, sheet_name=name, engine="calamine", has_header=False,
                read_options={"dtypes": "string", "n_rows": 25},
            )
        except Exception:
            continue
        score = probe.width * probe.height
        if score > best_score:
            best, best_score = name, score
    return best


def _flatten_record(record: Any, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if isinstance(record, dict):
        for key, value in record.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                flat.update(_flatten_record(value, path))
            elif isinstance(value, list):
                flat[path] = json.dumps(value, separators=(",", ":"))
            else:
                flat[path] = value
    else:
        flat[prefix or "value"] = record
    return flat


def _records_from_json(path: Path, encoding: str) -> list[Any]:
    text = path.read_bytes().decode(encoding, errors="replace").strip()
    if not text:
        return []
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    parsed = json.loads(text)
    if isinstance(parsed, list):
        return parsed
    # A wrapped payload keeps its records under the longest list value.
    if isinstance(parsed, dict):
        lists = [(k, v) for k, v in parsed.items() if isinstance(v, list)]
        if lists:
            return max(lists, key=lambda kv: len(kv[1]))[1]
    return [parsed]


def read_json(path: Path) -> LandedFrame:
    from app.ingest.sniff import detect_encoding

    encoding = detect_encoding(path)
    records = _records_from_json(path, encoding)
    flattened = [_flatten_record(r) for r in records]

    columns: list[str] = []
    for row in flattened:
        for key in row:
            if key not in columns:
                columns.append(key)

    data = {
        name: [
            None if (v := row.get(name)) is None else (v if isinstance(v, str) else json.dumps(v)
            if isinstance(v, list | dict) else str(v))
            for row in flattened
        ]
        for name in columns
    }
    frame = pl.DataFrame(data, schema={name: pl.Utf8 for name in columns})

    return LandedFrame(
        frame=_with_src_row(frame, 1),
        kind="json",
        sheet=None,
        sniff={"encoding": encoding, "records": len(records), "columns": columns},
    )


def land(path: Path, sheet: str | None = None) -> LandedFrame:
    kind = detect_kind(path)
    if kind == "csv":
        return read_csv(path)
    if kind == "excel":
        return read_excel(path, sheet=sheet)
    return read_json(path)
