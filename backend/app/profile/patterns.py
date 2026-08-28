from collections import Counter

MAX_PATTERN_LENGTH = 60
MAX_DISTINCT_SAMPLED = 20_000


def _char_class(char: str) -> str | None:
    if char.isdigit():
        return r"\d"
    if char.isalpha():
        return "[A-Z]" if char.isupper() else "[a-z]"
    if char.isspace():
        return r"\s"
    return None


def mask(value: str) -> str:
    """Collapse a value into a regex-shaped mask: 'ORD-000123' -> '[A-Z]{3}-\\d{6}'.

    The mask doubles as a candidate validation regex, which is why literals are escaped
    rather than merely described.
    """
    if not value:
        return ""
    parts: list[str] = []
    run_class: str | None = None
    run_length = 0

    def flush() -> None:
        if run_class is None:
            return
        parts.append(run_class if run_length == 1 else f"{run_class}{{{run_length}}}")

    for char in value[:MAX_PATTERN_LENGTH]:
        current = _char_class(char)
        if current is not None and current == run_class:
            run_length += 1
            continue
        flush()
        if current is None:
            run_class, run_length = None, 0
            parts.append(_escape(char))
        else:
            run_class, run_length = current, 1
    flush()

    suffix = "..." if len(value) > MAX_PATTERN_LENGTH else ""
    return "".join(parts) + suffix


def _escape(char: str) -> str:
    return f"\\{char}" if char in r".^$*+?()[]{}|\/" else char


def mine(counts: list[tuple[str, int]]) -> tuple[list[dict], float]:
    """Aggregate value->count pairs into ranked masks plus the fraction of rows covered."""
    sampled = counts[:MAX_DISTINCT_SAMPLED]
    total = sum(c for _, c in counts)
    covered = sum(c for _, c in sampled)
    if not total:
        return [], 0.0

    tally: Counter[str] = Counter()
    for value, count in sampled:
        tally[mask(value)] += count

    ranked = [
        {"pattern": pattern, "count": count, "pct": round(count / covered, 4)}
        for pattern, count in tally.most_common(10)
    ]
    return ranked, round(covered / total, 4)
