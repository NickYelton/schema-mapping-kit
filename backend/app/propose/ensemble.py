"""Combine the proposers into one ranked answer per target field.

The weights below are not guesses. Measured against the four fixtures (50 columns whose
correct target is known), the name-and-profile heuristic gets 50/50 top-1 and the embedding
provider 40/50, the latter failing hardest on the German sheet where a English-trained
static model has nothing useful to say. So the heuristic leads, the LLM — which generalises
to vocabulary nobody wrote an alias for — comes second, and embeddings are a tiebreaker
rather than a decider.

Providers that did not run are excluded from the denominator instead of scoring zero, so a
machine with no embedding model and no recorded LLM response still gets calibrated
confidences from the heuristic alone rather than uniformly deflated ones.
"""

from app.models.mapping import Candidate, Evidence, FieldMapping, MappingSpec
from app.models.target import TargetSchema
from app.propose.providers import embeddings, heuristic, llm

WEIGHTS: dict[str, float] = {
    "heuristic": 0.45,
    "llm": 0.35,
    "embedding": 0.20,
}

# Agreement is the reason to run an ensemble at all: two providers independently reaching
# the same field is worth more than either one's raw score.
AGREEMENT_BONUS = 0.08

# Below this, propose nothing and let the reviewer decide. A wrong mapping presented
# confidently costs more than an obvious blank.
MIN_CONFIDENCE = 0.35


def _merge(
    per_provider: dict[str, dict[str, list[Candidate]]],
    columns: list[str],
) -> dict[str, list[Candidate]]:
    """Fold each provider's candidates into one ranked list per source column."""
    ran = [name for name, result in per_provider.items() if result]
    denominator = sum(WEIGHTS[name] for name in ran) or 1.0

    merged: dict[str, list[Candidate]] = {}
    for column in columns:
        pooled: dict[str, dict] = {}
        for provider, result in per_provider.items():
            for candidate in result.get(column, []):
                entry = pooled.setdefault(
                    candidate.target_field,
                    {"weighted": 0.0, "evidence": [], "transforms": candidate.transforms},
                )
                entry["weighted"] += WEIGHTS[provider] * candidate.score
                entry["evidence"].extend(candidate.provenance)
                # Prefer the richest transform suggestion available for this pairing.
                if len(candidate.transforms) > len(entry["transforms"]):
                    entry["transforms"] = candidate.transforms

        candidates = []
        for target_field, entry in pooled.items():
            providers = {e.provider for e in entry["evidence"]}
            score = entry["weighted"] / denominator
            score += AGREEMENT_BONUS * (len(providers) - 1)
            candidates.append(
                Candidate(
                    target_field=target_field,
                    score=round(min(score, 1.0), 4),
                    transforms=entry["transforms"],
                    provenance=sorted(entry["evidence"], key=lambda e: -e.score),
                )
            )
        candidates.sort(key=lambda c: (-c.score, c.target_field))
        merged[column] = candidates
    return merged


def _assign(
    merged: dict[str, list[Candidate]], schema: TargetSchema
) -> tuple[dict[str, tuple[str, Candidate]], list[str]]:
    """Greedily match source columns to target fields, best pairing first.

    A target field takes at most one source column, and a column fills at most one field.
    That constraint is what resolves `orders_nested.jsonl`, which carries both `quantity`
    and a drifted `qty`: the stronger claim wins the field and the loser is reported as
    unmapped for a human to look at, rather than silently overwriting it.
    """
    pairings = [
        (candidate.score, column, candidate)
        for column, candidates in merged.items()
        for candidate in candidates
        if candidate.score >= MIN_CONFIDENCE
    ]
    pairings.sort(key=lambda p: (-p[0], p[1], p[2].target_field))

    taken_fields: dict[str, tuple[str, Candidate]] = {}
    taken_columns: set[str] = set()
    for _, column, candidate in pairings:
        if column in taken_columns or candidate.target_field in taken_fields:
            continue
        taken_fields[candidate.target_field] = (column, candidate)
        taken_columns.add(column)

    unmapped = sorted(set(merged) - taken_columns)
    return taken_fields, unmapped


def propose(
    profiles: list[dict],
    schema: TargetSchema,
    source_id: str,
    use_llm: bool = True,
    use_embeddings: bool = True,
) -> MappingSpec:
    columns = [p["name"] for p in profiles]

    per_provider: dict[str, dict[str, list[Candidate]]] = {
        "heuristic": heuristic.propose(profiles, schema),
        "embedding": embeddings.propose(profiles, schema) if use_embeddings else {},
        "llm": llm.propose(profiles, schema, source_id=source_id) if use_llm else {},
    }

    merged = _merge(per_provider, columns)
    assigned, unmapped = _assign(merged, schema)

    mappings: list[FieldMapping] = []
    for field in schema.fields:
        match = assigned.get(field.name)
        if match is None:
            mappings.append(
                FieldMapping(
                    target_field=field.name,
                    confidence=0.0,
                    note="No source column proposed for this field.",
                )
            )
            continue
        column, candidate = match
        mappings.append(
            FieldMapping(
                target_field=field.name,
                source_column=column,
                transforms=candidate.transforms,
                confidence=candidate.score,
                provenance=candidate.provenance,
                alternatives=[
                    alternative
                    for alternative in merged[column]
                    if alternative.target_field != field.name
                ][:2],
            )
        )

    return MappingSpec(
        source_id=source_id,
        target_schema=schema.name,
        target_version=schema.version,
        mappings=mappings,
        unmapped_columns=unmapped,
    )


def provider_status(use_llm: bool = True, use_embeddings: bool = True) -> list[dict]:
    """Which voices were available, so the UI can say what was missing and why."""
    from app.core.settings import get_settings

    settings = get_settings()
    status = [{"provider": "heuristic", "available": True, "detail": "always available"}]

    if not use_embeddings:
        detail, ok = "disabled for this request", False
    elif embeddings.available():
        detail, ok = f"model {settings.embedding_model}", True
    else:
        detail, ok = "model not cached locally — run `make fetch-model`", False
    status.append({"provider": "embedding", "available": ok, "detail": detail})

    if not use_llm:
        detail, ok = "disabled for this request", False
    elif settings.llm_provider.lower() == "replay":
        # `replay` is configured but useless without recordings, and reporting it as
        # available when the fixtures directory is empty would credit the ensemble with a
        # voice that contributed nothing.
        recorded = (
            len(list(settings.llm_fixtures.glob("*.json")))
            if (settings.llm_fixtures.is_dir())
            else 0
        )
        if recorded:
            detail, ok = f"replaying {recorded} recorded response(s)", True
        else:
            detail, ok = (
                "replay has no recorded responses — run `make record-llm` with an API key",
                False,
            )
    else:
        detail, ok = f"provider {settings.llm_provider}", True
    status.append({"provider": "llm", "available": ok, "detail": detail})
    return status


def evidence_summary(mapping: FieldMapping) -> str:
    """One line a reviewer can read, e.g. `heuristic 0.97; embedding 0.61`."""
    parts = [f"{e.provider} {e.score:.2f}" for e in mapping.provenance]
    return "; ".join(parts) if parts else "no evidence"


__all__ = ["propose", "provider_status", "evidence_summary", "Evidence", "WEIGHTS"]
