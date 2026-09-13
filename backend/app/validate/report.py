"""Validate a transformed frame and explain each rejection in terms of the customer's file."""

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import pandera.errors as pe
import polars as pl

from app.ingest.readers import SRC_ROW
from app.models.mapping import FieldMapping, MappingSpec
from app.models.target import TargetField, TargetSchema
from app.profile.semantics import DATE_FORMATS, is_null_token
from app.target import pandera_schema

MAX_VALUE_CHARS = 60
MAX_GROUP_LINES = 20
DATE_LABELS = dict(DATE_FORMATS)

KIND_FOR_CONSTRAINT = {
    "enum": "not_allowed",
    "pattern": "pattern",
    "min": "below_min",
    "max": "above_max",
    "length": "length",
}


class StructuralError(ValueError):
    """The transform output is malformed, so no individual row can be judged."""


@dataclass
class Problem:
    line: int
    field: str
    column: str | None
    value: str | None
    kind: str
    clause: str

    @property
    def message(self) -> str:
        return f"Line {self.line}: {self.clause}"

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "field": self.field,
            "column": self.column,
            "value": self.value,
            "kind": self.kind,
            "message": self.message,
        }


@dataclass
class Report:
    source_id: str
    target: str
    spec_version: int
    content_hash: str
    rows_in: int
    valid: pl.DataFrame
    rejected: pl.DataFrame
    problems: list[Problem]

    @property
    def rows_valid(self) -> int:
        return self.valid.height

    @property
    def rows_rejected(self) -> int:
        return self.rejected.height

    def summary(self) -> str:
        if not self.problems:
            return f"All {self.rows_in} rows passed."
        rows = "row was" if self.rows_rejected == 1 else "rows were"
        problems = "problem" if len(self.problems) == 1 else "problems"
        return (
            f"{self.rows_valid} of {self.rows_in} rows passed. {self.rows_rejected} {rows}"
            f" rejected for {len(self.problems)} {problems}."
        )

    def groups(self) -> list[dict]:
        buckets: dict[str, list[Problem]] = {}
        for problem in self.problems:
            buckets.setdefault(problem.clause, []).append(problem)
        groups = [
            {
                "field": members[0].field,
                "column": members[0].column,
                "kind": members[0].kind,
                "value": members[0].value,
                "count": len(members),
                "lines": [p.line for p in members[:MAX_GROUP_LINES]],
                "message": _sentence(clause),
            }
            for clause, members in buckets.items()
        ]
        return sorted(groups, key=lambda g: (-g["count"], g["lines"][0]))

    def to_dict(self, run_id: str | None = None) -> dict:
        return {
            "run_id": run_id,
            "source_id": self.source_id,
            "target": self.target,
            "spec_version": self.spec_version,
            "content_hash": self.content_hash,
            "rows_in": self.rows_in,
            "rows_valid": self.rows_valid,
            "rows_rejected": self.rows_rejected,
            "summary": self.summary(),
            "groups": self.groups(),
            "problems": [p.to_dict() for p in self.problems],
        }


def check(
    frame: pl.DataFrame, landed: pl.DataFrame, spec: MappingSpec, schema: TargetSchema
) -> Report:
    _check_shape(frame, landed, schema)

    try:
        pandera_schema.build(schema, strict=False).validate(frame, lazy=True)
        failures = None
    except pe.SchemaErrors as exc:
        failures = exc.failure_cases

    problems = _problems(failures, frame, landed, spec, schema) if failures is not None else []
    rejected_lines = pl.Series(sorted({p.line for p in problems}), dtype=pl.Int64)
    in_rejected = pl.col(SRC_ROW).is_in(rejected_lines)

    return Report(
        source_id=spec.source_id,
        target=schema.slug,
        spec_version=spec.version,
        content_hash=spec.content_hash(),
        rows_in=landed.height,
        valid=frame.filter(~in_rejected),
        rejected=frame.filter(in_rejected),
        problems=problems,
    )


def _check_shape(frame: pl.DataFrame, landed: pl.DataFrame, schema: TargetSchema) -> None:
    expected = {SRC_ROW, *schema.field_names}
    missing = sorted(expected - set(frame.columns))
    extra = sorted(set(frame.columns) - expected)
    if missing or extra:
        raise StructuralError(
            f"transform output does not match {schema.slug}: missing columns {missing},"
            f" unexpected columns {extra}"
        )
    if SRC_ROW not in landed.columns:
        raise StructuralError(f"landed frame has no {SRC_ROW}, so rows cannot be traced")


def _problems(
    failures: pl.DataFrame,
    frame: pl.DataFrame,
    landed: pl.DataFrame,
    spec: MappingSpec,
    schema: TargetSchema,
) -> list[Problem]:
    structural = failures.filter(pl.col("index").is_null())
    if structural.height:
        details = "; ".join(
            f"{r['column']}: {r['check']} ({r['failure_case']})"
            for r in structural.iter_rows(named=True)
        )
        raise StructuralError(f"transform output does not match {schema.slug}: {details}")

    lines = frame[SRC_ROW].to_list()
    wanted = pl.Series(sorted({lines[i] for i in failures["index"].to_list()}), dtype=pl.Int64)
    originals = {
        row[SRC_ROW]: row
        for row in landed.filter(pl.col(SRC_ROW).is_in(wanted)).iter_rows(named=True)
    }

    problems: list[Problem] = []
    for failure in failures.iter_rows(named=True):
        position = failure["index"]
        if failure["schema_context"] == "DataFrameSchema":
            if failure["check"] != "multiple_fields_uniqueness":
                raise StructuralError(f"unexpected frame-level failure: {failure['check']}")
            problems.append(_duplicate_key(frame, spec, schema, position, lines))
            continue

        field = schema.field(failure["column"])
        mapping = spec.mapping(field.name)
        line = lines[position]
        original = _original(mapping, originals.get(line, {}))

        if failure["check"] == "not_nullable":
            kind = _null_kind(mapping, original)
        elif failure["check_number"] is not None:
            constraint = pandera_schema.check_kinds(field.constraints)[failure["check_number"]]
            kind = KIND_FOR_CONSTRAINT[constraint]
        else:
            kind = "duplicate_value"

        transformed = frame[field.name][position]
        problems.append(
            Problem(
                line=line,
                field=field.name,
                column=mapping.source_column,
                value=original,
                kind=kind,
                clause=_clause(kind, field, mapping, original, transformed),
            )
        )

    order = {name: i for i, name in enumerate(schema.field_names)}
    return sorted(problems, key=lambda p: (p.line, order.get(p.field.split("+")[0], 0)))


def _original(mapping: FieldMapping, row: dict) -> str | None:
    if mapping.source_column is not None:
        value = row.get(mapping.source_column)
        return None if value is None else str(value)
    return mapping.literal


def _null_kind(mapping: FieldMapping, original: str | None) -> str:
    if not mapping.is_mapped:
        return "unmapped"
    if original is None or is_null_token(original):
        return "missing"
    return "unparseable"


def _clause(
    kind: str,
    field: TargetField,
    mapping: FieldMapping,
    original: str | None,
    transformed: object,
) -> str:
    name = field.name.replace("_", " ")
    where = _where(mapping, name)
    shown = _quote(original)
    c = field.constraints

    match kind:
        case "unmapped":
            return f"no column in the file provides {name}, which is required."
        case "missing":
            return f"{where} is empty, but {name} is required."
        case "unparseable":
            return f"{where} is {shown}, which {_expected(field, mapping)}."
        case "not_allowed":
            return (
                f"{where} is {shown}, which is not an accepted {name}."
                f" Use one of: {', '.join(c.enum or [])}."
            )
        case "pattern":
            hint = f" (expected something like {field.examples[0]})" if field.examples else ""
            return f"{where} is {shown}, which does not look like a valid {name}{hint}."
        case "below_min":
            return f"{where} is {shown}, but {name} must be at least {_number(c.min)}."
        case "above_max":
            return f"{where} is {shown}, but {name} must be at most {_number(c.max)}."
        case "length":
            size = len(str(transformed)) if transformed is not None else 0
            if c.max_length is not None and size > c.max_length:
                limit = f"allows at most {c.max_length}"
            else:
                limit = f"needs at least {c.min_length}"
            return f"{where} is {size} characters long, but {name} {limit}."
        case "duplicate_value":
            return (
                f"{where} is {shown}, which appears on more than one line,"
                f" but {name} must be unique."
            )
    raise ValueError(f"no message for problem kind {kind!r}")


def _expected(field: TargetField, mapping: FieldMapping) -> str:
    match field.dtype:
        case "date" | "datetime":
            fmt = next(
                (op.args["format"] for op in mapping.transforms if op.op == "parse_date"),
                "%Y-%m-%d",
            )
            return f"is not a valid date in the format {DATE_LABELS.get(fmt, fmt)}"
        case "integer":
            return "is not a whole number"
        case "decimal":
            return "is not a number"
        case "boolean":
            return "is not a yes/no value"
    return "could not be read"


def _duplicate_key(
    frame: pl.DataFrame, spec: MappingSpec, schema: TargetSchema, position: int, lines: list[int]
) -> Problem:
    keys = schema.primary_key
    row = frame.row(position, named=True)
    same_key = pl.all_horizontal(pl.col(k).eq_missing(row[k]) for k in keys)
    others = [line for line in frame.filter(same_key)[SRC_ROW].to_list() if line != lines[position]]
    described = " with ".join(f"{k.replace('_', ' ')} {row[k]}" for k in keys)
    columns = [spec.mapping(k).source_column for k in keys]

    return Problem(
        line=lines[position],
        field="+".join(keys),
        column=", ".join(c for c in columns if c) or None,
        value=", ".join(str(row[k]) for k in keys),
        kind="duplicate_key",
        clause=f"{described} also appears on {_line_list(others)}.",
    )


def _where(mapping: FieldMapping, name: str) -> str:
    if mapping.source_column is not None:
        return f'"{mapping.source_column}"'
    if mapping.literal is not None:
        return "the fixed value"
    return name


def _quote(value: str | None) -> str:
    if value is None:
        return "empty"
    if len(value) > MAX_VALUE_CHARS:
        value = value[: MAX_VALUE_CHARS - 1] + "…"
    return f'"{value}"'


def _number(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def _line_list(lines: list[int]) -> str:
    if len(lines) == 1:
        return f"line {lines[0]}"
    head = ", ".join(str(line) for line in lines[:-1])
    return f"lines {head} and {lines[-1]}"


def _sentence(clause: str) -> str:
    return clause[0].upper() + clause[1:] if clause[:1].isalpha() else clause


def rejections_path(report_path: str | Path) -> str:
    path = Path(report_path)
    return str(path.with_name(path.name.replace("_report.json", "_rejections.csv")))


def write(report: Report, directory: Path, stem: str, run_id: str | None = None) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)

    report_path = directory / f"{stem}_report.json"
    report_path.write_text(
        json.dumps(report.to_dict(run_id=run_id), indent=2, default=str) + "\n", encoding="utf-8"
    )

    csv_path = Path(rejections_path(report_path))
    # utf-8-sig so Excel, where most customers will open this, reads umlauts correctly.
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["line", "column", "value", "problem"])
        for problem in report.problems:
            writer.writerow(
                [problem.line, problem.column or "", problem.value or "", _sentence(problem.clause)]
            )

    return {"report": report_path, "rejections": csv_path}
