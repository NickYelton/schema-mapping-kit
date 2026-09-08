from fastapi import APIRouter, Body, HTTPException, Query

from app.ingest import landing
from app.models.mapping import MappingSpec
from app.propose import ensemble, store
from app.target import loader

router = APIRouter(prefix="/sources/{source_id}", tags=["propose"])


def _profiles_or_404(source_id: str) -> list[dict]:
    if landing.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}")
    profiles = landing.get_profiles(source_id)
    if not profiles:
        raise HTTPException(status_code=409, detail=f"Source {source_id!r} has no profiled columns")
    return profiles


def _schema_or_404(name: str, version: int | None):
    try:
        return loader.get(name, version)
    except loader.SchemaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No schema {name!r}") from exc


@router.post("/propose")
def propose(
    source_id: str,
    schema: str = Query(default="orders"),
    version: int | None = Query(default=None, ge=1),
    use_llm: bool = Query(default=True),
    use_embeddings: bool = Query(default=True),
    save: bool = Query(default=True, description="Persist the proposal as version 1"),
) -> dict:
    """Run the ensemble and return a draft MappingSpec.

    The draft is saved by default so the transform compiler has something to read without a
    review step existing yet. Saving is content-addressed, so re-proposing an unchanged file
    does not pile up versions.
    """
    profiles = _profiles_or_404(source_id)
    target = _schema_or_404(schema, version)

    spec = ensemble.propose(
        profiles, target, source_id=source_id, use_llm=use_llm, use_embeddings=use_embeddings
    )
    stored = store.save(spec) if save else None

    return {
        "source_id": source_id,
        "target_schema": target.name,
        "target_version": target.version,
        "mapped": spec.mapped_count,
        "total": len(spec.mappings),
        "unmapped_columns": spec.unmapped_columns,
        "providers": ensemble.provider_status(use_llm=use_llm, use_embeddings=use_embeddings),
        "spec_id": stored["id"] if stored else None,
        "version": stored["version"] if stored else None,
        "content_hash": spec.content_hash(),
        "spec": spec.model_dump(mode="json"),
    }


@router.get("/spec")
def current_spec(source_id: str) -> dict:
    if landing.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}")
    current = store.head(source_id)
    if current is None:
        raise HTTPException(
            status_code=404, detail=f"No mapping spec for {source_id!r} — run /propose first"
        )
    return current


@router.get("/spec/history")
def spec_history(source_id: str) -> list[dict]:
    if landing.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}")
    return store.history(source_id)


@router.put("/spec")
def update_spec(source_id: str, spec: MappingSpec = Body(...)) -> dict:
    """Save a corrected spec. This is the endpoint the phase 7 review UI will call."""
    if landing.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}")
    if spec.source_id != source_id:
        raise HTTPException(
            status_code=400,
            detail=f"spec.source_id {spec.source_id!r} does not match the URL {source_id!r}",
        )
    try:
        loader.get(spec.target_schema, spec.target_version)
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"unknown target schema {spec.target_schema}_v{spec.target_version}",
        ) from exc

    valid = set(loader.get(spec.target_schema, spec.target_version).field_names)
    unknown = sorted({m.target_field for m in spec.mappings} - valid)
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown target fields: {unknown}")

    return store.save(spec, created_by="human")
