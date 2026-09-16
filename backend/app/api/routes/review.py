"""Endpoints behind the review UI: preview a draft spec without saving it, and help edit it."""

from collections import Counter

import polars as pl
from fastapi import APIRouter, Body, HTTPException, Query

from app.ingest import landing
from app.ingest.readers import SRC_ROW
from app.models.mapping import DECIMAL_LOCALES, OP_ARGS, FieldMapping, MappingSpec
from app.profile.semantics import DATE_FORMATS
from app.propose.providers import heuristic
from app.target import loader
from app.transform import pipeline, polars_engine
from app.transform.pipeline import CompileError
from app.validate import report as validation

router = APIRouter(prefix="/sources/{source_id}", tags=["review"])
vocabulary_router = APIRouter(tags=["review"])


@vocabulary_router.get("/vocabulary")
def vocabulary() -> dict:
    return {
        "ops": [
            {"op": op, "required": sorted(required), "optional": sorted(optional)}
            for op, (required, optional) in OP_ARGS.items()
        ],
        "date_formats": [{"format": fmt, "label": label} for fmt, label in DATE_FORMATS],
        "decimal_locales": sorted(DECIMAL_LOCALES),
    }


def _require_source(source_id: str) -> None:
    if landing.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}")


@router.post("/spec/preview")
def preview(
    source_id: str,
    spec: MappingSpec = Body(...),
    samples: int = Query(default=5, ge=0, le=50),
) -> dict:
    _require_source(source_id)
    if spec.source_id != source_id:
        raise HTTPException(
            status_code=400,
            detail=f"spec.source_id {spec.source_id!r} does not match the URL {source_id!r}",
        )
    try:
        schema = loader.get(spec.target_schema, spec.target_version)
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"unknown target schema {spec.target_schema}_v{spec.target_version}",
        ) from exc

    landed = landing.load_frame(source_id)
    missing = [c for c in pipeline.required_columns(spec) if c not in landed.columns]
    if missing:
        raise HTTPException(
            status_code=422, detail=f"no column named {', '.join(map(repr, missing))} in the file"
        )

    try:
        frame = polars_engine.apply(spec, landed, schema)
        checked = validation.check(frame, landed, spec, schema)
    except (CompileError, validation.StructuralError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except pl.exceptions.PolarsError as exc:
        raise HTTPException(status_code=422, detail=f"the transform failed: {exc}") from exc

    field_problems: Counter[str] = Counter()
    for problem in checked.problems:
        for name in problem.field.split("+"):
            field_problems[name] += 1

    return {
        "content_hash": spec.content_hash(),
        "rows_in": checked.rows_in,
        "rows_valid": checked.rows_valid,
        "rows_rejected": checked.rows_rejected,
        "summary": checked.summary(),
        "groups": checked.groups(),
        "field_problems": dict(field_problems),
        "samples": _samples(spec, frame.head(samples), landed.head(samples)),
    }


def _samples(spec: MappingSpec, head: pl.DataFrame, landed: pl.DataFrame) -> dict:
    originals = {row[SRC_ROW]: row for row in landed.iter_rows(named=True)}
    lines = head[SRC_ROW].to_list()
    return {
        mapping.target_field: [
            {
                "line": line,
                "raw": _raw(mapping, originals.get(line, {})),
                "value": None if value is None else str(value),
            }
            for line, value in zip(lines, head[mapping.target_field].to_list(), strict=True)
        ]
        for mapping in spec.mappings
    }


def _raw(mapping: FieldMapping, row: dict) -> str | None:
    if mapping.source_column is not None:
        value = row.get(mapping.source_column)
        return None if value is None else str(value)
    return mapping.literal


@router.get("/suggest")
def suggest(
    source_id: str,
    column: str = Query(...),
    field: str = Query(...),
    schema: str = Query(default="orders"),
    version: int | None = Query(default=None, ge=1),
) -> dict:
    _require_source(source_id)
    profile = next((p for p in landing.get_profiles(source_id) if p["name"] == column), None)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No column {column!r} in {source_id!r}")
    try:
        target = loader.get(schema, version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No schema {schema!r}") from exc
    try:
        target_field = target.field(field)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No field {field!r} in {target.slug}") from exc

    ops = heuristic.suggest_transforms(profile, target_field)
    return {
        "column": column,
        "field": field,
        "transforms": [op.model_dump(mode="json") for op in ops],
    }
