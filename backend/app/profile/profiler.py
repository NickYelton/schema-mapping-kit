import polars as pl

from app.ingest.readers import SRC_ROW
from app.profile import patterns, semantics

TOP_VALUES = 10
SAMPLE_VALUES = 5


def _type_candidates(values: list[str]) -> dict[str, float]:
    if not values:
        return {}
    total = len(values)
    checks = {
        "integer": semantics.parses_as_integer,
        "decimal": semantics.parses_as_decimal,
        "european_decimal": semantics.parses_as_european_decimal,
        "boolean": semantics.parses_as_boolean,
    }
    scores = {
        name: round(sum(1 for v in values if check(v)) / total, 4)
        for name, check in checks.items()
    }
    dates = semantics.date_candidates(values)
    scores["date"] = dates[0]["pct"] if dates else 0.0
    return {name: score for name, score in scores.items() if score > 0}


def _infer_type(scores: dict[str, float]) -> tuple[str, float]:
    """Pick the narrowest type that explains nearly every value.

    Order matters: integers also parse as decimals, so a permissive check must not win over
    a stricter one that covers the same values.
    """
    for name in ("boolean", "integer", "date", "european_decimal", "decimal"):
        if scores.get(name, 0.0) >= 0.95:
            return name, scores[name]
    if scores:
        best = max(scores.items(), key=lambda kv: kv[1])
        if best[1] >= 0.6:
            return best[0], best[1]
    return "string", 1.0


def profile_column(frame: pl.DataFrame, name: str, ordinal: int) -> dict:
    series = frame.get_column(name)
    row_count = series.len()

    stripped = series.cast(pl.Utf8).str.strip_chars()
    non_null = [v for v in stripped.to_list() if v is not None and not semantics.is_null_token(v)]
    null_count = row_count - len(non_null)
    blank_count = sum(
        1 for v in stripped.to_list() if v is not None and v == ""
    )

    distinct_count = len(set(non_null))
    normalized_distinct_count = len({v.casefold() for v in non_null})
    cardinality_ratio = round(distinct_count / len(non_null), 4) if non_null else 0.0

    counts = (
        pl.Series(non_null, dtype=pl.Utf8)
        .value_counts(sort=True)
        .rows()
        if non_null
        else []
    )
    ranked = [(str(v), int(c)) for v, c in counts]

    mined, coverage = patterns.mine(ranked)
    scores = _type_candidates(non_null)
    inferred, confidence = _infer_type(scores)
    dates = semantics.date_candidates(non_null) if non_null else []

    lengths = [len(v) for v in non_null]
    numeric = _numeric_stats(non_null, inferred)

    return {
        "name": name,
        "ordinal": ordinal,
        "row_count": row_count,
        "null_count": null_count,
        "null_rate": round(null_count / row_count, 4) if row_count else 0.0,
        "blank_count": blank_count,
        "distinct_count": distinct_count,
        "normalized_distinct_count": normalized_distinct_count,
        "cardinality_ratio": cardinality_ratio,
        "inferred_type": inferred,
        "type_confidence": confidence,
        "type_candidates": scores,
        "min_length": min(lengths) if lengths else 0,
        "max_length": max(lengths) if lengths else 0,
        "numeric": numeric,
        "date_candidates": dates,
        "date_ambiguous": len(dates) > 1 and dates[0]["parsed"] == dates[1]["parsed"],
        "top_values": [
            {"value": v, "count": c, "pct": round(c / len(non_null), 4)}
            for v, c in ranked[:TOP_VALUES]
        ],
        "patterns": mined,
        "pattern_coverage": coverage,
        "semantics": semantics.classify(
            non_null, distinct_count, cardinality_ratio, normalized_distinct_count
        ),
        "samples": [v for v, _ in ranked[:SAMPLE_VALUES]],
    }


def _numeric_stats(values: list[str], inferred: str) -> dict | None:
    if inferred not in {"integer", "decimal", "european_decimal"}:
        return None
    numbers: list[float] = []
    for value in values:
        candidate = value.strip().lstrip("$€£¥").rstrip("%")
        if inferred == "european_decimal":
            candidate = candidate.replace(".", "").replace(",", ".")
        else:
            candidate = candidate.replace(",", "")
        try:
            numbers.append(float(candidate))
        except ValueError:
            continue
    if not numbers:
        return None
    mean = sum(numbers) / len(numbers)
    variance = sum((n - mean) ** 2 for n in numbers) / len(numbers)
    return {
        "min": round(min(numbers), 6),
        "max": round(max(numbers), 6),
        "mean": round(mean, 6),
        "stddev": round(variance**0.5, 6),
    }


def profile_frame(frame: pl.DataFrame) -> list[dict]:
    columns = [c for c in frame.columns if c != SRC_ROW]
    return [profile_column(frame, name, index) for index, name in enumerate(columns)]
