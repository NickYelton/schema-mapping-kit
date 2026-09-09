"""Semantic matching with model2vec static embeddings.

Two things make this provider unusual, and both are deliberate.

**It never reaches the network.** The model is resolved from the local HuggingFace cache
with `local_files_only`, so a machine that has never run `make fetch-model` reports the
provider unavailable instead of blocking a request on a 260MB download. The README promises
the loop runs offline; a lazy download on first use would quietly break that promise.

**It degrades instead of failing.** When the model is absent the proposer drops to
heuristics and the LLM, and the ensemble records that this voice was missing. A mapping tool
that refuses to open because an optional model is not cached would be worse than one that
proposes slightly less well.
"""

import hashlib
import logging
from functools import lru_cache
from typing import Any

import numpy as np

from app.core.settings import get_settings
from app.db import duck
from app.models.mapping import Candidate, Evidence
from app.models.target import TargetSchema
from app.propose.providers.heuristic import suggest_transforms

log = logging.getLogger(__name__)

SAMPLE_VALUES = 4
MIN_SCORE = 0.15


class Unavailable(RuntimeError):
    """The embedding model is not present locally."""


@lru_cache
def _model() -> Any:
    """Load the static model from the local cache, or raise Unavailable."""
    name = get_settings().embedding_model
    try:
        from huggingface_hub import snapshot_download
        from model2vec import StaticModel
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise Unavailable(f"model2vec is not installed: {exc}") from exc

    try:
        path = snapshot_download(name, local_files_only=True)
    except Exception as exc:
        raise Unavailable(
            f"embedding model {name!r} is not in the local cache — run `make fetch-model`"
        ) from exc

    # force_download defaults to True upstream, which would re-fetch on every call.
    return StaticModel.from_pretrained(path, force_download=False)


def available() -> bool:
    try:
        _model()
    except Unavailable:
        return False
    return True


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _load_cached(digests: list[str], model_name: str) -> dict[str, np.ndarray]:
    if not digests:
        return {}
    placeholders = ", ".join("?" for _ in digests)
    with duck.session() as conn:
        rows = conn.execute(
            f"SELECT content_hash, vector FROM embedding_cache"
            f" WHERE model = ? AND content_hash IN ({placeholders})",
            [model_name, *digests],
        ).fetchall()
    return {row[0]: np.asarray(row[1], dtype=np.float32) for row in rows}


def _store_cached(vectors: dict[str, np.ndarray], model_name: str) -> None:
    if not vectors:
        return
    with duck.session() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO embedding_cache (content_hash, model, vector) VALUES (?, ?, ?)",
            [[digest, model_name, vec.tolist()] for digest, vec in vectors.items()],
        )


def embed(texts: list[str]) -> np.ndarray:
    """Embed texts, reading and writing the DuckDB cache.

    The cache matters more than it looks: a target schema's field descriptions are identical
    on every proposal, so after the first run only the source columns are ever encoded.
    """
    settings = get_settings()
    model_name = settings.embedding_model
    digests = [_digest(t) for t in texts]

    cached = _load_cached(sorted(set(digests)), model_name)
    missing = [t for t, d in zip(texts, digests, strict=True) if d not in cached]

    if missing:
        unique = list(dict.fromkeys(missing))
        fresh = _model().encode(unique)
        new = {
            _digest(t): np.asarray(v, dtype=np.float32) for t, v in zip(unique, fresh, strict=True)
        }
        _store_cached(new, model_name)
        cached.update(new)

    return np.vstack([cached[d] for d in digests])


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


def column_text(profile: dict) -> str:
    """The text describing a source column: its name, then evidence of what it holds."""
    name = profile["name"].replace("_", " ").replace(".", " ")
    parts = [name]
    samples = [str(v) for v in profile.get("samples", [])[:SAMPLE_VALUES]]
    if samples:
        parts.append("values such as " + ", ".join(samples))
    return ". ".join(parts)


def propose(
    profiles: list[dict], schema: TargetSchema, limit: int = 3
) -> dict[str, list[Candidate]]:
    """Rank target fields per column by cosine similarity, or return {} when unavailable."""
    if not profiles:
        return {}
    try:
        _model()
    except Unavailable as exc:
        log.info("embedding provider unavailable: %s", exc)
        return {}

    field_texts = [f.embedding_text() for f in schema.fields]
    column_texts = [column_text(p) for p in profiles]

    matrix = _normalize(embed(field_texts + column_texts))
    fields = matrix[: len(field_texts)]
    columns = matrix[len(field_texts) :]

    similarity = columns @ fields.T

    out: dict[str, list[Candidate]] = {}
    for row, profile in enumerate(profiles):
        ranked = np.argsort(-similarity[row])[:limit]
        candidates = []
        for index in ranked:
            score = float(similarity[row][index])
            if score < MIN_SCORE:
                continue
            field = schema.fields[index]
            candidates.append(
                Candidate(
                    target_field=field.name,
                    score=round(min(max(score, 0.0), 1.0), 4),
                    transforms=suggest_transforms(profile, field),
                    provenance=[
                        Evidence(
                            provider="embedding",
                            score=round(min(max(score, 0.0), 1.0), 4),
                            detail=f"cosine {score:.2f} against the {field.name} description",
                        )
                    ],
                )
            )
        out[profile["name"]] = candidates
    return out
