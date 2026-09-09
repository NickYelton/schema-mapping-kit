"""The LLM proposer, with a replay provider that keeps the demo offline.

`LLM_PROVIDER` selects the backend:

* `replay` (default) — serve a recorded response from `fixtures/llm/`, keyed by a hash of
  the request. No API key, no network. This is what makes a fresh clone work.
* `anthropic` — the Anthropic SDK.
* `openrouter` — raw HTTP, since OpenRouter is not an Anthropic endpoint.

Every live call is recorded to the `llm_calls` table, and `scripts/record_llm.py` promotes
those recordings into committed fixtures. The request hash is computed from the profile and
target schema only, so re-running the same file against the same schema replays cleanly
while any real change misses the fixture and says so.
"""

import hashlib
import json
import logging
import time
import uuid
from typing import Any

from app.core.settings import get_settings
from app.db import duck
from app.models.mapping import Candidate, Evidence
from app.models.target import TargetSchema
from app.propose.providers.heuristic import suggest_transforms

log = logging.getLogger(__name__)

MAX_SAMPLES = 5
MAX_TOKENS = 16000

SYSTEM = """You map columns from a messy customer data file onto a canonical target schema.

You are given a profile of each source column (its name, inferred type, semantic tags, and
sample values) and the fields of the target schema. For each source column, name the target
field it should populate, or null if nothing in the target fits.

Judge by what the values actually are, not only by the column name. Names are often
abbreviated, renamed, in another language, or meaningless (a column called X7 holding order
statuses maps to status). Two source columns may map to the same target field. Do not invent
target fields that are not in the schema."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_column": {"type": "string"},
                    "target_field": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "reasoning": {"type": "string"},
                },
                "required": ["source_column", "target_field", "confidence", "reasoning"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["mappings"],
    "additionalProperties": False,
}


class LLMUnavailable(RuntimeError):
    """No recorded response and no configured live provider."""


def build_request(profiles: list[dict], schema: TargetSchema) -> dict[str, Any]:
    """The provider-independent description of what we are asking.

    Only stable, meaning-bearing fields go in. Row counts and null counts are deliberately
    excluded: they change when a customer sends an updated extract of the same shape, and
    that should not invalidate a recorded response.
    """
    return {
        "target": {
            "name": schema.name,
            "version": schema.version,
            "fields": [
                {
                    "name": f.name,
                    "dtype": f.dtype,
                    "description": " ".join(f.description.split()),
                    "enum": f.constraints.enum,
                }
                for f in schema.fields
            ],
        },
        "columns": [
            {
                "name": p["name"],
                "inferred_type": p.get("inferred_type"),
                "semantics": sorted(p.get("semantics", [])),
                "samples": [str(s) for s in p.get("samples", [])[:MAX_SAMPLES]],
            }
            for p in profiles
        ],
    }


def request_hash(request: dict[str, Any]) -> str:
    blob = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _fixture_path(digest: str):
    return get_settings().llm_fixtures / f"{digest}.json"


def load_fixture(digest: str) -> dict | None:
    path = _fixture_path(digest)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))["response"]
    except (json.JSONDecodeError, KeyError) as exc:
        log.warning("ignoring malformed LLM fixture %s: %s", path.name, exc)
        return None


def save_fixture(digest: str, request: dict, response: dict, provider: str, model: str) -> None:
    path = _fixture_path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "request_hash": digest,
                "provider": provider,
                "model": model,
                "request": request,
                "response": response,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _record(
    source_id: str | None,
    provider: str,
    model: str,
    digest: str,
    request: dict,
    response: dict | None,
    error: str | None,
    latency_ms: int,
) -> None:
    with duck.session() as conn:
        conn.execute(
            """
            INSERT INTO llm_calls
                (id, source_id, provider, model, request_hash, request, response, error,
                 latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                uuid.uuid4().hex[:12],
                source_id,
                provider,
                model,
                digest,
                json.dumps(request),
                json.dumps(response) if response is not None else None,
                error,
                latency_ms,
            ],
        )


def _user_prompt(request: dict) -> str:
    return (
        "Target schema:\n"
        + json.dumps(request["target"], indent=2)
        + "\n\nSource columns:\n"
        + json.dumps(request["columns"], indent=2)
    )


def _call_anthropic(request: dict) -> dict:
    import anthropic

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": _user_prompt(request)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _call_openrouter(request: dict) -> dict:
    """OpenRouter is not an Anthropic endpoint, so this is plain HTTP rather than the SDK."""
    import httpx

    settings = get_settings()
    payload = {
        "model": settings.openrouter_model,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _user_prompt(request)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "mappings", "strict": True, "schema": RESPONSE_SCHEMA},
        },
    }
    with httpx.Client(timeout=120.0) as client:
        reply = client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json=payload,
        )
        reply.raise_for_status()
    return json.loads(reply.json()["choices"][0]["message"]["content"])


def fetch(request: dict, source_id: str | None = None) -> dict:
    """Return the raw LLM response, from a fixture or a live call."""
    settings = get_settings()
    provider = settings.llm_provider.lower()
    digest = request_hash(request)

    if provider == "replay":
        recorded = load_fixture(digest)
        if recorded is None:
            raise LLMUnavailable(
                f"no recorded response for request {digest}. Record one with"
                " `uv run python scripts/record_llm.py`, or set LLM_PROVIDER to a live"
                " provider."
            )
        return recorded

    started = time.monotonic()
    try:
        if provider == "anthropic":
            response = _call_anthropic(request)
            model = settings.anthropic_model
        elif provider == "openrouter":
            response = _call_openrouter(request)
            model = settings.openrouter_model
        else:
            raise LLMUnavailable(f"unknown LLM_PROVIDER {settings.llm_provider!r}")
    except LLMUnavailable:
        raise
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        _record(source_id, provider, "", digest, request, None, str(exc), elapsed)
        raise LLMUnavailable(f"{provider} call failed: {exc}") from exc

    elapsed = int((time.monotonic() - started) * 1000)
    _record(source_id, provider, model, digest, request, response, None, elapsed)
    return response


def propose(
    profiles: list[dict],
    schema: TargetSchema,
    source_id: str | None = None,
) -> dict[str, list[Candidate]]:
    """Ask the LLM, or return {} when it has nothing to say.

    Unavailability is not an error here. The proposer is an ensemble, and a missing fixture
    for a file nobody has recorded should degrade to heuristics plus embeddings rather than
    fail the request.
    """
    if not profiles:
        return {}

    request = build_request(profiles, schema)
    try:
        response = fetch(request, source_id=source_id)
    except LLMUnavailable as exc:
        log.info("llm provider unavailable: %s", exc)
        return {}

    valid = set(schema.field_names)
    by_column = {p["name"]: p for p in profiles}
    out: dict[str, list[Candidate]] = {}

    for entry in response.get("mappings", []):
        column = entry.get("source_column")
        target = entry.get("target_field")
        if column not in by_column or target is None:
            continue
        if target not in valid:
            # The model named a field that does not exist. Dropping it is the whole reason
            # the target schema is validated separately from the proposal.
            log.warning("llm proposed unknown target field %r for column %r", target, column)
            continue
        score = float(entry.get("confidence", 0.0))
        score = min(max(score, 0.0), 1.0)
        out.setdefault(column, []).append(
            Candidate(
                target_field=target,
                score=round(score, 4),
                transforms=suggest_transforms(by_column[column], schema.field(target)),
                provenance=[
                    Evidence(
                        provider="llm",
                        score=round(score, 4),
                        detail=str(entry.get("reasoning", ""))[:300],
                    )
                ],
            )
        )
    return out
