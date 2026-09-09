from fastapi import APIRouter, HTTPException, Query

from app.ingest import landing
from app.models.mapping import MappingSpec
from app.propose import store
from app.target import loader
from app.transform import polars_engine, runner, sql
from app.transform.pipeline import CompileError

router = APIRouter(prefix="/sources/{source_id}/transform", tags=["transform"])


def _spec_and_schema(source_id: str) -> tuple[MappingSpec, object]:
    if landing.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}")
    current = store.head(source_id)
    if current is None:
        raise HTTPException(
            status_code=409, detail=f"No mapping spec for {source_id!r} — run /propose first"
        )
    spec = MappingSpec.model_validate(current["spec"])
    try:
        schema = loader.get(spec.target_schema, spec.target_version)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"spec targets missing schema {spec.target_schema}_v{spec.target_version}",
        ) from exc
    return spec, schema


@router.get("/sql")
def compiled_sql(source_id: str) -> dict:
    spec, schema = _spec_and_schema(source_id)
    source_path = str(landing.landed_path(source_id))
    try:
        return {"engine": "duckdb", "source": sql.compile_script(spec, schema, source_path)}
    except CompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/python")
def compiled_python(source_id: str) -> dict:
    spec, schema = _spec_and_schema(source_id)
    try:
        return {"engine": "polars", "source": polars_engine.render(spec, schema)}
    except CompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("")
def execute(
    source_id: str,
    engine: str = Query(default="polars", pattern="^(polars|duckdb)$"),
    limit: int = Query(default=20, ge=0, le=200),
) -> dict:
    spec, schema = _spec_and_schema(source_id)
    try:
        result = runner.run(spec, schema, engine=engine)
    except CompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    preview = result.frame.head(limit)
    return {
        "run_id": result.run_id,
        "engine": result.engine,
        "rows_in": result.rows_in,
        "rows_out": result.rows_out,
        "output_path": str(result.output_path),
        "sql_path": str(result.sql_path),
        "python_path": str(result.python_path),
        "columns": preview.columns,
        "rows": [[None if v is None else str(v) for v in row] for row in preview.rows()],
    }


@router.get("/runs")
def runs(source_id: str) -> list[dict]:
    if landing.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}")
    return runner.list_runs(source_id)
