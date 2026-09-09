"""MappingSpec -> Polars expressions, and the equivalent standalone Python module.

`build` is what runs; `render` is what ships. They are generated from the same op list and a
test asserts they produce identical frames, which is the only thing keeping them honest.
"""

import polars as pl

from app.ingest.readers import SRC_ROW
from app.models.mapping import FieldMapping, MappingSpec, TransformOp
from app.models.target import TargetSchema
from app.transform import pipeline
from app.transform.pipeline import CompileError

POLARS_TYPES = {
    "string": pl.Utf8,
    "integer": pl.Int64,
    "decimal": pl.Float64,
    "boolean": pl.Boolean,
    "date": pl.Date,
    "datetime": pl.Datetime,
}

TYPE_SOURCE = {
    "string": "pl.Utf8",
    "integer": "pl.Int64",
    "decimal": "pl.Float64",
    "boolean": "pl.Boolean",
    "date": "pl.Date",
    "datetime": "pl.Datetime",
}


def _apply(expr: pl.Expr, op: TransformOp, dtype: str) -> pl.Expr:
    match op.op:
        case "strip":
            return expr.str.strip_chars()
        case "lower":
            return expr.str.to_lowercase()
        case "upper":
            return expr.str.to_uppercase()
        case "null_if":
            tokens = pipeline.null_tokens(op)
            return (
                pl.when(expr.str.strip_chars().str.to_lowercase().is_in(tokens))
                .then(None)
                .otherwise(expr)
            )
        case "strip_currency":
            return expr.str.replace_all(pipeline.CURRENCY_RE, "").str.strip_chars()
        case "parse_decimal":
            if op.args.get("locale", "us") == "eu":
                return expr.str.replace_all(".", "", literal=True).str.replace_all(
                    ",", ".", literal=True
                )
            return expr.str.replace_all(",", "", literal=True)
        case "parse_percent":
            return expr.str.replace_all(pipeline.PERCENT_RE, "").str.strip_chars()
        case "parse_date":
            out = pipeline.ISO_DATETIME if dtype == "datetime" else pipeline.ISO_DATE
            return expr.str.strptime(
                pl.Datetime, format=op.args["format"], strict=False
            ).dt.strftime(out)
        case "map_values":
            mapping = {str(k): str(v) for k, v in op.args["mapping"].items()}
            fallback = op.args.get("default")
            if fallback is not None:
                return expr.replace_strict(mapping, default=str(fallback))
            return expr.replace(mapping)
        case "regex_extract":
            return expr.str.extract(op.args["pattern"], int(op.args.get("group", 1)))
        case "default":
            return expr.fill_null(str(op.args["value"]))
        case "cast":
            return _cast(expr, op.args["dtype"])
    raise CompileError(f"no Polars expression for op {op.op!r}")


def _cast(expr: pl.Expr, dtype: str) -> pl.Expr:
    if dtype == "date":
        return expr.str.to_date(format=pipeline.ISO_DATE, strict=False)
    if dtype == "datetime":
        return expr.str.to_datetime(format=pipeline.ISO_DATETIME, strict=False)
    try:
        return expr.cast(POLARS_TYPES[dtype], strict=False)
    except KeyError as exc:
        raise CompileError(f"no Polars type for dtype {dtype!r}") from exc


def _source(src: str, op: TransformOp, dtype: str) -> str:
    match op.op:
        case "strip":
            return f"{src}.str.strip_chars()"
        case "lower":
            return f"{src}.str.to_lowercase()"
        case "upper":
            return f"{src}.str.to_uppercase()"
        case "null_if":
            tokens = pipeline.null_tokens(op)
            return (
                f"pl.when({src}.str.strip_chars().str.to_lowercase()"
                f".is_in({tokens!r})).then(None).otherwise({src})"
            )
        case "strip_currency":
            return f"{src}.str.replace_all({pipeline.CURRENCY_RE!r}, '').str.strip_chars()"
        case "parse_decimal":
            if op.args.get("locale", "us") == "eu":
                return (
                    f"{src}.str.replace_all('.', '', literal=True)"
                    ".str.replace_all(',', '.', literal=True)"
                )
            return f"{src}.str.replace_all(',', '', literal=True)"
        case "parse_percent":
            return f"{src}.str.replace_all({pipeline.PERCENT_RE!r}, '').str.strip_chars()"
        case "parse_date":
            out = pipeline.ISO_DATETIME if dtype == "datetime" else pipeline.ISO_DATE
            return (
                f"{src}.str.strptime(pl.Datetime, format={op.args['format']!r},"
                f" strict=False).dt.strftime({out!r})"
            )
        case "map_values":
            mapping = {str(k): str(v) for k, v in op.args["mapping"].items()}
            fallback = op.args.get("default")
            if fallback is not None:
                return f"{src}.replace_strict({mapping!r}, default={str(fallback)!r})"
            return f"{src}.replace({mapping!r})"
        case "regex_extract":
            return f"{src}.str.extract({op.args['pattern']!r}, {int(op.args.get('group', 1))})"
        case "default":
            return f"{src}.fill_null({str(op.args['value'])!r})"
        case "cast":
            return _cast_source(src, op.args["dtype"])
    raise CompileError(f"no Polars source for op {op.op!r}")


def _cast_source(src: str, dtype: str) -> str:
    if dtype == "date":
        return f"{src}.str.to_date(format={pipeline.ISO_DATE!r}, strict=False)"
    if dtype == "datetime":
        return f"{src}.str.to_datetime(format={pipeline.ISO_DATETIME!r}, strict=False)"
    return f"{src}.cast({TYPE_SOURCE[dtype]}, strict=False)"


def column_expression(mapping: FieldMapping, schema: TargetSchema) -> pl.Expr:
    field = schema.field(mapping.target_field)
    if mapping.literal is not None:
        return _cast(pl.lit(mapping.literal, dtype=pl.Utf8), field.dtype).alias(field.name)
    if mapping.source_column is None:
        return pl.lit(None, dtype=POLARS_TYPES[field.dtype]).alias(field.name)

    expr = pl.col(mapping.source_column)
    for op in mapping.transforms:
        expr = _apply(expr, op, field.dtype)
    return expr.alias(field.name)


def column_source(mapping: FieldMapping, schema: TargetSchema) -> str:
    field = schema.field(mapping.target_field)
    if mapping.literal is not None:
        base = _cast_source(f"pl.lit({mapping.literal!r}, dtype=pl.Utf8)", field.dtype)
        return f"{base}.alias({field.name!r})"
    if mapping.source_column is None:
        return f"pl.lit(None, dtype={TYPE_SOURCE[field.dtype]}).alias({field.name!r})"

    src = f"pl.col({mapping.source_column!r})"
    for op in mapping.transforms:
        src = _source(src, op, field.dtype)
    return f"{src}.alias({field.name!r})"


def build(spec: MappingSpec, schema: TargetSchema, include_src_row: bool = True) -> list[pl.Expr]:
    pipeline.validate_spec(spec, schema)
    exprs = [pl.col(SRC_ROW)] if include_src_row else []
    exprs += [column_expression(spec.mapping(f.name), schema) for f in schema.fields]
    return exprs


def apply(spec: MappingSpec, frame: pl.DataFrame, schema: TargetSchema) -> pl.DataFrame:
    return frame.select(build(spec, schema, include_src_row=SRC_ROW in frame.columns))


def render(spec: MappingSpec, schema: TargetSchema) -> str:
    pipeline.validate_spec(spec, schema)
    lines = [f"        pl.col({SRC_ROW!r}),"]
    lines += [f"        {column_source(spec.mapping(f.name), schema)}," for f in schema.fields]
    body = "\n".join(lines)
    return f'''"""Generated by schema-mapping-kit — do not edit.

{spec.target_schema} v{spec.target_version} · spec version {spec.version}
source {spec.source_id} · content hash {spec.content_hash()}
"""

import polars as pl

SOURCE_COLUMNS = {pipeline.required_columns(spec)!r}


def transform(frame: pl.DataFrame) -> pl.DataFrame:
    keep_src_row = {SRC_ROW!r} in frame.columns
    exprs = [
{body}
    ]
    return frame.select(exprs if keep_src_row else exprs[1:])


if __name__ == "__main__":
    import sys

    source = sys.argv[1]
    frame = (
        pl.read_parquet(source)
        if source.endswith(".parquet")
        else pl.read_csv(source, infer_schema_length=0)
    )
    print(transform(frame))
'''
