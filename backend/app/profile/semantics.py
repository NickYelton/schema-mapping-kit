import re
from datetime import datetime

NULL_TOKENS = {"", "-", "--", "n/a", "na", "null", "none", "nil", "#n/a", "?", "unknown"}

BOOL_TOKENS = {"true", "false", "yes", "no", "y", "n", "t", "f", "0", "1"}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
URL_RE = re.compile(r"^(https?://|www\.)\S+$", re.I)
CURRENCY_RE = re.compile(r"^[\$€£¥]\s?-?[\d.,]+$|^-?[\d.,]+\s?(USD|EUR|GBP|JPY|CAD)$", re.I)
PERCENT_RE = re.compile(r"^-?[\d.,]+\s?%$")
INT_RE = re.compile(r"^-?\d{1,3}(,\d{3})*$|^-?\d+$")

DATE_FORMATS = [
    ("%Y-%m-%d", "YYYY-MM-DD"),
    ("%m/%d/%Y", "MM/DD/YYYY"),
    ("%d/%m/%Y", "DD/MM/YYYY"),
    ("%d.%m.%Y", "DD.MM.YYYY"),
    ("%Y/%m/%d", "YYYY/MM/DD"),
    ("%m-%d-%Y", "MM-DD-YYYY"),
    ("%d-%m-%Y", "DD-MM-YYYY"),
    ("%d-%b-%Y", "DD-Mon-YYYY"),
    ("%b %d, %Y", "Mon DD, YYYY"),
    ("%Y-%m-%dT%H:%M:%S%z", "ISO 8601"),
    ("%Y-%m-%dT%H:%M:%SZ", "ISO 8601 (Z)"),
    ("%Y-%m-%d %H:%M:%S", "YYYY-MM-DD HH:MM:SS"),
]


def is_null_token(value: str) -> bool:
    return value.strip().lower() in NULL_TOKENS


def _strip_thousands(value: str) -> str:
    return value.strip().lstrip("$€£¥").rstrip("%").replace(",", "").replace(" ", "")


def parses_as_integer(value: str) -> bool:
    return bool(INT_RE.match(value.strip()))


def parses_as_decimal(value: str) -> bool:
    candidate = _strip_thousands(value)
    if not candidate or candidate in {"-", "."}:
        return False
    try:
        float(candidate)
    except ValueError:
        return False
    return True


def parses_as_european_decimal(value: str) -> bool:
    """Match 1.234,56 and 0,85 — a comma decimal separator with optional dot grouping."""
    candidate = value.strip().lstrip("$€£¥")
    if "," not in candidate:
        return False
    return bool(re.match(r"^-?\d{1,3}(\.\d{3})*,\d+$|^-?\d+,\d+$", candidate))


def parses_as_boolean(value: str) -> bool:
    return value.strip().lower() in BOOL_TOKENS


def date_candidates(values: list[str]) -> list[dict]:
    """Rank strptime formats by how many values they parse.

    Formats that tie are all returned: DD/MM vs MM/DD is genuinely undecidable when every
    day-of-month in the sample is 12 or lower, and the reviewer needs to see that.
    """
    if not values:
        return []
    scored: list[dict] = []
    for fmt, label in DATE_FORMATS:
        parsed = 0
        for value in values:
            try:
                datetime.strptime(value.strip(), fmt)
            except (ValueError, TypeError):
                continue
            parsed += 1
        if parsed:
            scored.append(
                {
                    "format": fmt,
                    "label": label,
                    "parsed": parsed,
                    "pct": round(parsed / len(values), 4),
                }
            )
    scored.sort(key=lambda c: (-c["parsed"], c["format"]))
    return scored[:4]


def classify(
    values: list[str],
    distinct_count: int,
    cardinality_ratio: float,
    normalized_distinct_count: int | None = None,
) -> list[str]:
    """Label a column with every semantic tag its values support.

    Enum detection uses the case-folded distinct count: a status column spelled SHIPPED,
    Shipped, and shipped is still an enum, and inconsistent casing is precisely the mess
    this tool exists to absorb.
    """
    if not values:
        return []
    total = len(values)
    tags: list[str] = []
    enum_distinct = (
        distinct_count if normalized_distinct_count is None else normalized_distinct_count
    )
    enum_ratio = enum_distinct / total

    def fraction(predicate) -> float:
        return sum(1 for v in values if predicate(v)) / total

    if fraction(lambda v: bool(EMAIL_RE.match(v.strip()))) > 0.8:
        tags.append("email")
    if fraction(lambda v: bool(URL_RE.match(v.strip()))) > 0.8:
        tags.append("url")
    if fraction(lambda v: bool(CURRENCY_RE.match(v.strip()))) > 0.6:
        tags.append("currency")
    if fraction(lambda v: bool(PERCENT_RE.match(v.strip()))) > 0.6:
        tags.append("percentage")
    if fraction(parses_as_european_decimal) > 0.5:
        tags.append("european_decimal")

    dates = date_candidates(values)
    if dates and dates[0]["pct"] > 0.7:
        tags.append("date")

    if fraction(parses_as_boolean) > 0.9 and enum_distinct <= 3:
        tags.append("boolean")
    if enum_distinct <= 20 and enum_ratio < 0.6 and total >= 3:
        tags.append("enum")
    # Uniqueness is not concludable from a handful of values, so require a real sample.
    if cardinality_ratio > 0.9 and distinct_count > 3 and total >= 10:
        tags.append("identifier")
    if enum_distinct < distinct_count:
        tags.append("inconsistent_case")

    return tags
