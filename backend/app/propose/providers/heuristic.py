"""Name- and profile-based matching. No model, no network, no API key.

Two signals, deliberately kept separate:

* the column's **name**, fuzzy-matched against the target's aliases, and
* the column's **profile** — the values themselves.

The second is what earns this provider its place. `orders_messy.csv` names its status column
`X7`, which tells a name matcher nothing; but the profile says it holds five repeating
lower-cased-if-you-squint tokens that are exactly the target's enum, which is conclusive.
"""

import re

from rapidfuzz import fuzz

from app.models.mapping import Candidate, Evidence, TransformOp
from app.models.target import TargetField, TargetSchema

NAME_WEIGHT = 0.6
VALUE_WEIGHT = 0.4


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def name_variants(column: str) -> list[str]:
    """The forms of a column name worth matching against.

    Ingest produces compound names from two directions — a merged Excel header becomes
    `Bestellung_Kundennr` and a nested JSON path becomes `customer.id` — and in both the
    meaning sits in the last segment while the prefix names the group. Matching the whole
    string alone lets `Bestellung_` dilute `Kundennr` until it scores below a pattern
    coincidence, so the trailing segment is tried too and the better score wins.
    """
    normalized = _normalize(column)
    variants = [normalized]
    if "_" in normalized:
        tail = normalized.rsplit("_", 1)[-1]
        if tail and tail != normalized:
            variants.append(tail)
    return variants


def name_score(column: str, field: TargetField) -> tuple[float, str, bool]:
    """Best fuzzy match between the column name and the field's names/aliases.

    Returns the score, the alias that produced it, and whether it was an *exact* alias hit.
    Exactness is tracked separately from the score because a trailing-segment match is
    discounted but still exact: `Artikel_Artikelnr` names the sku unambiguously, and
    without that distinction the discount would sink it below an unrelated column whose
    values happen to fit the order_id pattern.
    """
    aliases = field.normalized_aliases()
    canonical = _normalize(field.name)
    best, best_alias, best_exact = 0.0, "", False

    for index, variant in enumerate(name_variants(column)):
        # A trailing segment is real but weaker evidence than the full name matching:
        # `item.name` should still prefer product_name over a bare `name` coincidence.
        discount = 1.0 if index == 0 else 0.95
        for alias in aliases:
            exact = alias == variant
            # An exact hit on the field's own name outranks one on an abbreviation.
            # `orders_nested.jsonl` carries both `quantity` and a drifted `qty`, and both
            # match the quantity field exactly — without this the winner is whichever
            # sorts first, which is not a decision anyone made.
            ratio = (
                (1.0 if alias == canonical else 0.98)
                if exact
                else (fuzz.token_sort_ratio(variant, alias) / 100.0)
            )
            # A short name like `ln` or `pos` fuzzy-matches far too much, so an inexact
            # hit on a tiny string is halved. An exact hit is still exact.
            if len(variant) <= 3 and not exact:
                ratio *= 0.5
            score = ratio * discount
            if score > best:
                best, best_alias, best_exact = score, alias, exact

    return best, best_alias, best_exact


def _enum_overlap(profile: dict, field: TargetField) -> float:
    """Fraction of the column's distinct values that are already canonical enum members."""
    if not field.constraints.enum:
        return 0.0
    values = {str(v["value"]).strip().casefold() for v in profile.get("top_values", [])}
    if not values:
        return 0.0
    canonical = {e.casefold() for e in field.constraints.enum}
    return len(values & canonical) / len(values)


def _pattern_match(profile: dict, field: TargetField) -> float:
    """Do the column's actual values satisfy the target's regex?"""
    pattern = field.constraints.pattern
    if not pattern:
        return 0.0
    samples = [str(v["value"]) for v in profile.get("top_values", [])]
    if not samples:
        return 0.0
    compiled = re.compile(pattern)
    return sum(1 for s in samples if compiled.match(s.strip())) / len(samples)


def _type_agreement(profile: dict, field: TargetField) -> float:
    """Does the inferred physical type line up with the target dtype?"""
    inferred = profile.get("inferred_type", "string")
    tags = set(profile.get("semantics", []))
    match field.dtype:
        case "integer":
            return 1.0 if inferred == "integer" else 0.0
        case "decimal":
            if inferred in {"decimal", "european_decimal", "integer"}:
                return 1.0
            return 0.6 if {"currency", "percentage"} & tags else 0.0
        case "date" | "datetime":
            return 1.0 if inferred == "date" or "date" in tags else 0.0
        case "boolean":
            return 1.0 if inferred == "boolean" else 0.0
        case "string":
            # Everything is representable as a string, so this is weak evidence at best.
            return 0.3
    return 0.0


def value_score(profile: dict, field: TargetField) -> tuple[float, str]:
    """Evidence drawn from the values rather than the header."""
    enum = _enum_overlap(profile, field)
    if enum >= 0.6:
        return enum, f"{enum:.0%} of values are already {field.name} enum members"

    pattern = _pattern_match(profile, field)
    if pattern >= 0.8:
        return pattern, f"{pattern:.0%} of values match the {field.name} pattern"

    agreement = _type_agreement(profile, field)
    detail = f"values look like {profile.get('inferred_type', 'string')}, target is {field.dtype}"
    return agreement, detail


def propose_column(profile: dict, schema: TargetSchema, limit: int = 3) -> list[Candidate]:
    column = profile["name"]
    scored: list[Candidate] = []

    for field in schema.fields:
        by_name, alias, exact = name_score(column, field)
        by_value, value_detail = value_score(profile, field)
        combined = NAME_WEIGHT * by_name + VALUE_WEIGHT * by_value

        # A plain string target carries no pattern or enum to corroborate a match, so its
        # value evidence tops out at 0.3 and an exact name hit would otherwise cap at 0.72
        # — below what a coincidental pattern match scores elsewhere. When the name matches
        # exactly and the values do not contradict it, treat that as near-conclusive.
        if exact and by_value > 0:
            combined = max(combined, 0.92 * (by_name if by_name < 1.0 else 1.0))

        if combined < 0.15:
            continue

        detail_parts = []
        if by_name > 0.5:
            hit = "exact" if exact else f"{by_name:.0%}"
            detail_parts.append(f"name {hit} vs alias {alias!r}")
        if by_value > 0.5:
            detail_parts.append(value_detail)
        if not detail_parts:
            detail_parts.append(value_detail)

        scored.append(
            Candidate(
                target_field=field.name,
                score=round(min(combined, 1.0), 4),
                transforms=suggest_transforms(profile, field),
                provenance=[
                    Evidence(
                        provider="heuristic",
                        score=round(min(combined, 1.0), 4),
                        detail="; ".join(detail_parts),
                    )
                ],
            )
        )

    scored.sort(key=lambda c: (-c.score, c.target_field))
    return scored[:limit]


def suggest_transforms(profile: dict, field: TargetField) -> list[TransformOp]:
    """Derive the cleanup steps the profile implies for this target field.

    Everything here is read off phase 2's profile rather than guessed: the null tokens it
    counted, the date format it ranked first, the european-decimal tag it set.
    """
    ops: list[TransformOp] = [TransformOp(op="strip")]
    tags = set(profile.get("semantics", []))
    inferred = profile.get("inferred_type", "string")

    if profile.get("null_count", 0) or profile.get("blank_count", 0):
        ops.append(TransformOp(op="null_if", args={"tokens": ["", "-", "N/A", "n/a", "null"]}))

    if field.dtype in {"integer", "decimal"}:
        if "currency" in tags:
            ops.append(TransformOp(op="strip_currency"))
        if "percentage" in tags:
            ops.append(TransformOp(op="parse_percent"))
        if "european_decimal" in tags or inferred == "european_decimal":
            ops.append(TransformOp(op="parse_decimal", args={"locale": "eu"}))
        elif field.dtype == "decimal":
            ops.append(TransformOp(op="parse_decimal", args={"locale": "us"}))

    elif field.dtype in {"date", "datetime"}:
        candidates = profile.get("date_candidates") or []
        if candidates:
            ops.append(TransformOp(op="parse_date", args={"format": candidates[0]["format"]}))

    elif field.dtype == "string" and field.constraints.enum:
        mapping = _enum_mapping(profile, field)
        if mapping:
            ops.append(TransformOp(op="map_values", args={"mapping": mapping}))
        elif "inconsistent_case" in tags:
            ops.append(TransformOp(op="lower"))

    ops.append(TransformOp(op="cast", args={"dtype": field.dtype}))
    return ops


def _enum_mapping(profile: dict, field: TargetField) -> dict[str, str]:
    """Map the column's observed spellings onto canonical enum members.

    Only case-folding differences are resolved automatically. A genuinely foreign value
    (`retourniert`) is left out on purpose: guessing a translation is exactly the kind of
    silent wrong answer a reviewer would never catch, so it stays visible as an unmapped
    value the human has to decide on.
    """
    canonical = {e.casefold(): e for e in field.constraints.enum or []}
    mapping: dict[str, str] = {}
    for entry in profile.get("top_values", []):
        raw = str(entry["value"]).strip()
        target = canonical.get(raw.casefold())
        if target is not None and raw != target:
            mapping[raw] = target
    return mapping


def propose(profiles: list[dict], schema: TargetSchema) -> dict[str, list[Candidate]]:
    return {p["name"]: propose_column(p, schema) for p in profiles}
