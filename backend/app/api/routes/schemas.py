from fastapi import APIRouter, HTTPException, Query

from app.target import loader

router = APIRouter(prefix="/schemas", tags=["schemas"])


@router.get("")
def index() -> list[dict]:
    try:
        schemas = loader.load_all()
    except loader.SchemaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [
        {
            "slug": s.slug,
            "name": s.name,
            "version": s.version,
            "title": s.title,
            "description": s.description.strip(),
            "field_count": len(s.fields),
            "primary_key": s.primary_key,
        }
        for s in sorted(schemas.values(), key=lambda s: (s.name, s.version))
    ]


@router.get("/{name}")
def detail(name: str, version: int | None = Query(default=None, ge=1)) -> dict:
    """Fetch one schema. `name` accepts a bare name or a slug; version defaults to latest."""
    try:
        schema = loader.get(name, version)
    except loader.SchemaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No schema {name!r}") from exc
    return schema.model_dump(mode="json")
