"""MappingSpec -> a DuckDB SELECT over the landed Parquet."""

from app.ingest.readers import SRC_ROW
from app.models.mapping import MappingSpec, TransformOp
from app.models.target import TargetSchema
from app.transform import pipeline
from app.transform.pipeline import CompileError


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _apply(expr: str, op: TransformOp, dtype: str) -> str:
    match op.op:
        case "strip":
            return f"trim({expr})"
        case "lower":
            return f"lower({expr})"
        case "upper":
            return f"upper({expr})"
        case "null_if":
            tokens = ", ".join(quote(t) for t in pipeline.null_tokens(op))
            return f"(CASE WHEN lower(trim({expr})) IN ({tokens}) THEN NULL ELSE {expr} END)"
        case "strip_currency":
            return f"trim(regexp_replace({expr}, {quote(pipeline.CURRENCY_RE)}, '', 'g'))"
        case "parse_decimal":
            if op.args.get("locale", "us") == "eu":
                return f"replace(replace({expr}, '.', ''), ',', '.')"
            return f"replace({expr}, ',', '')"
        case "parse_percent":
            return f"trim(regexp_replace({expr}, {quote(pipeline.PERCENT_RE)}, '', 'g'))"
        case "parse_date":
            fmt = op.args["format"]
            out = pipeline.ISO_DATETIME if dtype == "datetime" else pipeline.ISO_DATE
            return f"strftime(try_strptime({expr}, {quote(fmt)}), {quote(out)})"
        case "map_values":
            arms = "".join(
                f" WHEN {quote(str(k))} THEN {quote(str(v))}" for k, v in op.args["mapping"].items()
            )
            fallback = op.args.get("default")
            tail = quote(str(fallback)) if fallback is not None else expr
            return f"(CASE {expr}{arms} ELSE {tail} END)"
        case "regex_extract":
            group = int(op.args.get("group", 1))
            return f"regexp_extract({expr}, {quote(op.args['pattern'])}, {group})"
        case "default":
            return f"coalesce({expr}, {quote(str(op.args['value']))})"
        case "cast":
            return _cast(expr, op.args["dtype"])
    raise CompileError(f"no SQL for op {op.op!r}")


def _cast(expr: str, dtype: str) -> str:
    try:
        return f"try_cast({expr} AS {pipeline.SQL_TYPES[dtype]})"
    except KeyError as exc:
        raise CompileError(f"no SQL type for dtype {dtype!r}") from exc


def column_expression(mapping, schema: TargetSchema) -> str:
    field = schema.field(mapping.target_field)
    if mapping.literal is not None:
        return _cast(quote(mapping.literal), field.dtype)
    if mapping.source_column is None:
        return f"CAST(NULL AS {pipeline.SQL_TYPES[field.dtype]})"

    expr = ident(mapping.source_column)
    for op in mapping.transforms:
        expr = _apply(expr, op, field.dtype)
    return expr


def compile_select(
    spec: MappingSpec,
    schema: TargetSchema,
    source: str,
    include_src_row: bool = True,
) -> str:
    pipeline.validate_spec(spec, schema)

    lines = []
    if include_src_row:
        lines.append(f"    {ident(SRC_ROW)}")
    for field in schema.fields:
        expression = column_expression(spec.mapping(field.name), schema)
        lines.append(f"    {expression} AS {ident(field.name)}")

    return (
        f"-- {spec.target_schema} v{spec.target_version} · spec version {spec.version}\n"
        f"-- source {spec.source_id} · content hash {spec.content_hash()}\n"
        "SELECT\n" + ",\n".join(lines) + f"\nFROM read_parquet({quote(source)})"
    )


def compile_script(spec: MappingSpec, schema: TargetSchema, source: str) -> str:
    return compile_select(spec, schema, source) + ";\n"
