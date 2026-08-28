from dataclasses import dataclass, field
from pathlib import Path

from charset_normalizer import from_bytes

CANDIDATE_DELIMITERS = [",", ";", "\t", "|"]
SNIFF_BYTES = 128 * 1024
SNIFF_LINES = 60
NULL_TOKENS = {"", "-", "n/a", "na", "null", "none", "nil", "#n/a", "?"}


@dataclass
class TextSniff:
    encoding: str
    delimiter: str
    header_row: int
    column_count: int
    preamble: list[str] = field(default_factory=list)


def detect_encoding(path: Path) -> str:
    raw = path.read_bytes()[:SNIFF_BYTES]
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    match = from_bytes(raw).best()
    if match is None:
        return "utf-8"
    encoding = (match.encoding or "utf-8").replace("-", "_").lower()
    if encoding in {"ascii", "utf_8"}:
        return "utf-8"
    # On short samples charset-normalizer picks arbitrarily among the windows-125x family.
    # cp1252 dominates real customer exports, and its undefined bytes make a strict decode a
    # reliable veto, so prefer it whenever the bytes actually are valid cp1252.
    if encoding.startswith(("cp125", "windows_125")):
        try:
            raw.decode("cp1252")
        except UnicodeDecodeError:
            return encoding
        return "cp1252"
    return encoding


def _split(line: str, delimiter: str) -> list[str]:
    return [cell.strip().strip('"') for cell in line.split(delimiter)]


def _looks_numeric(value: str) -> bool:
    stripped = value.strip().lstrip("$€£").rstrip("%").replace(",", "").replace(" ", "")
    if not stripped:
        return False
    try:
        float(stripped)
    except ValueError:
        return False
    return True


def detect_delimiter(lines: list[str]) -> str:
    best, best_score = ",", -1.0
    for delimiter in CANDIDATE_DELIMITERS:
        counts = [len(_split(line, delimiter)) for line in lines if line.strip()]
        if not counts:
            continue
        modal = max(set(counts), key=counts.count)
        if modal < 2:
            continue
        score = counts.count(modal) / len(counts) * modal
        if score > best_score:
            best, best_score = delimiter, score
    return best


def detect_header_row(lines: list[str], delimiter: str) -> tuple[int, int]:
    """Return (row index, column count) for the first row that looks like a real header.

    Customer exports routinely carry title/timestamp/blank rows above the header, so the
    header is the first row whose width matches the body's modal width and whose cells are
    all non-empty, non-numeric labels.
    """
    widths = [len(_split(line, delimiter)) if line.strip() else 0 for line in lines]
    populated = [w for w in widths if w >= 2]
    if not populated:
        return 0, max(widths) if widths else 0
    modal = max(set(populated), key=populated.count)

    for index, line in enumerate(lines):
        if widths[index] != modal:
            continue
        cells = _split(line, delimiter)
        if any(not cell for cell in cells):
            continue
        if any(_looks_numeric(cell) for cell in cells):
            continue
        if len({c.lower() for c in cells}) != len(cells):
            continue
        return index, modal

    first = next((i for i, w in enumerate(widths) if w == modal), 0)
    return first, modal


def sniff_text(path: Path) -> TextSniff:
    encoding = detect_encoding(path)
    text = path.read_bytes()[:SNIFF_BYTES].decode(encoding, errors="replace")
    lines = text.splitlines()[:SNIFF_LINES]
    delimiter = detect_delimiter(lines)
    header_row, column_count = detect_header_row(lines, delimiter)
    return TextSniff(
        encoding=encoding,
        delimiter=delimiter,
        header_row=header_row,
        column_count=column_count,
        preamble=lines[:header_row],
    )
