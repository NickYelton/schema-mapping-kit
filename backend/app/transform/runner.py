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
from app.validate import report as validation

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
    rows_valid: int | None = None
    rows_rejected: int | None = None
    report: validation.Report | None = None
    report_path: Path | None = None
    rejections_path: Path | None = None


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
    validate: bool = True,
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
    run_id = uuid.uuid4().hex[:12]

    checked = None
    output = frame
    if validate:
        checked = validation.check(frame, pl.read_parquet(source_path), spec, schema)
        output = checked.valid

    output_path = root / f"v{spec.version}_{spec.content_hash()}_{engine}.parquet"
    output.write_parquet(output_path)

    reports = validation.write(checked, root, f"run_{run_id}", run_id=run_id) if checked else {}

    if persist:
        with duck.session() as conn:
            conn.execute(
                """
                INSERT INTO runs
                    (id, spec_id, engine, input_path, output_path, rows_in, rows_out,
                     rows_rejected, report_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    spec.source_id,
                    engine,
                    source_path,
                    str(output_path),
                    source["row_count"],
                    frame.height,
                    checked.rows_rejected if checked else None,
                    str(reports["report"]) if checked else None,
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
        rows_valid=checked.rows_valid if checked else None,
        rows_rejected=checked.rows_rejected if checked else None,
        report=checked,
        report_path=reports.get("report"),
        rejections_path=reports.get("rejections"),
    )


RUN_COLUMNS = """
    id, spec_id, engine, input_path, output_path, rows_in, rows_out, rows_rejected,
    report_path, created_at
"""


def _run_dict(r: tuple) -> dict:
    return {
        "run_id": r[0],
        "source_id": r[1],
        "engine": r[2],
        "input_path": r[3],
        "output_path": r[4],
        "rows_in": r[5],
        "rows_out": r[6],
        "rows_rejected": r[7],
        "rows_valid": None if r[7] is None else r[6] - r[7],
        "report_path": r[8],
        "rejections_path": validation.rejections_path(r[8]) if r[8] else None,
        "created_at": r[9].isoformat() if r[9] else None,
    }


def list_runs(source_id: str) -> list[dict]:
    with duck.session() as conn:
        rows = conn.execute(
            f"SELECT {RUN_COLUMNS} FROM runs WHERE spec_id = ? ORDER BY created_at DESC",
            [source_id],
        ).fetchall()
    return [_run_dict(r) for r in rows]


def get_run(run_id: str) -> dict | None:
    with duck.session() as conn:
        row = conn.execute(f"SELECT {RUN_COLUMNS} FROM runs WHERE id = ?", [run_id]).fetchone()
    return _run_dict(row) if row else None
