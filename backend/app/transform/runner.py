"""Execute a compiled transform and persist its artifacts."""

import uuid
from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl

from app.core.settings import get_settings
from app.db import duck
from app.ingest import landing
from app.models.mapping import MappingSpec
from app.models.target import TargetSchema
from app.transform import polars_engine, sql
from app.transform.pipeline import CompileError

ENGINES = ("duckdb", "polars")


@dataclass
class RunResult:
    run_id: str
    engine: str
    frame: pl.DataFrame
    output_path: Path
    sql_path: Path
    python_path: Path
    rows_in: int
    rows_out: int


def artifacts_dir(source_id: str) -> Path:
    path = get_settings().artifacts / "transforms" / source_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def compile_artifacts(spec: MappingSpec, schema: TargetSchema, source_path: str) -> dict[str, Path]:
    root = artifacts_dir(spec.source_id)
    stem = f"v{spec.version}_{spec.content_hash()}"

    sql_path = root / f"{stem}.sql"
    sql_path.write_text(sql.compile_script(spec, schema, source_path), encoding="utf-8")

    python_path = root / f"{stem}.py"
    python_path.write_text(polars_engine.render(spec, schema), encoding="utf-8")

    return {"sql": sql_path, "python": python_path}


def _run_duckdb(spec: MappingSpec, schema: TargetSchema, source_path: str) -> pl.DataFrame:
    statement = sql.compile_select(spec, schema, source_path)
    with duckdb.connect() as conn:
        return conn.execute(statement).pl()


def _run_polars(spec: MappingSpec, schema: TargetSchema, source_path: str) -> pl.DataFrame:
    return polars_engine.apply(spec, pl.read_parquet(source_path), schema)


def run(
    spec: MappingSpec,
    schema: TargetSchema,
    engine: str = "polars",
    persist: bool = True,
) -> RunResult:
    if engine not in ENGINES:
        raise CompileError(f"unknown engine {engine!r}, expected one of {ENGINES}")

    source = landing.get_source(spec.source_id)
    if source is None:
        raise KeyError(spec.source_id)
    source_path = str(landing.landed_path(spec.source_id))

    paths = compile_artifacts(spec, schema, source_path)
    frame = (
        _run_duckdb(spec, schema, source_path)
        if engine == "duckdb"
        else _run_polars(spec, schema, source_path)
    )

    root = artifacts_dir(spec.source_id)
    output_path = root / f"v{spec.version}_{spec.content_hash()}_{engine}.parquet"
    frame.write_parquet(output_path)

    run_id = uuid.uuid4().hex[:12]
    if persist:
        with duck.session() as conn:
            conn.execute(
                """
                INSERT INTO runs
                    (id, spec_id, engine, input_path, output_path, rows_in, rows_out,
                     rows_rejected)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    spec.source_id,
                    engine,
                    source_path,
                    str(output_path),
                    source["row_count"],
                    frame.height,
                    None,
                ],
            )

    return RunResult(
        run_id=run_id,
        engine=engine,
        frame=frame,
        output_path=output_path,
        sql_path=paths["sql"],
        python_path=paths["python"],
        rows_in=source["row_count"],
        rows_out=frame.height,
    )


def list_runs(source_id: str) -> list[dict]:
    with duck.session() as conn:
        rows = conn.execute(
            """
            SELECT id, engine, input_path, output_path, rows_in, rows_out, created_at
            FROM runs WHERE spec_id = ? ORDER BY created_at DESC
            """,
            [source_id],
        ).fetchall()
    return [
        {
            "run_id": r[0],
            "engine": r[1],
            "input_path": r[2],
            "output_path": r[3],
            "rows_in": r[4],
            "rows_out": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]
