"""Shared contract for the transform compilers.

Every op takes a string and returns a string. `cast` is the single typed boundary and must
end each pipeline. That is what lets any op follow any other, keeps the SQL and Polars
compilers structurally identical, and confines type failure to one place.

Casts never raise. A value that will not convert becomes null, and phase 6 recovers the
original from the landed frame via `_src_row` to explain the rejection.
"""

from app.models.mapping import FieldMapping, MappingSpec
from app.models.target import TargetSchema

CURRENCY_RE = r"(?i)[$€£¥]|\s*(usd|eur|gbp|jpy|cad)\s*$"
PERCENT_RE = r"\s*%\s*$"

SQL_TYPES = {
    "string": "VARCHAR",
    "integer": "BIGINT",
    "decimal": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
}

ISO_DATE = "%Y-%m-%d"
ISO_DATETIME = "%Y-%m-%d %H:%M:%S"


class CompileError(ValueError):
    pass


def null_tokens(op) -> list[str]:
    """Case-folded, de-duplicated. The proposer suggests both `N/A` and `n/a`, which
    collapse to one token once folded."""
    seen: dict[str, None] = {}
    for token in op.args["tokens"]:
        seen.setdefault(str(token).strip().lower(), None)
    return list(seen)


def validate(mapping: FieldMapping, schema: TargetSchema) -> None:
    if not mapping.is_mapped:
        return

    field = schema.field(mapping.target_field)
    ops = mapping.transforms
    if not ops:
        raise CompileError(
            f"{mapping.target_field}: mapped columns need at least a cast to {field.dtype}"
        )

    casts = [i for i, op in enumerate(ops) if op.op == "cast"]
    if len(casts) != 1:
        raise CompileError(f"{mapping.target_field}: expected exactly one cast, found {len(casts)}")
    if casts[0] != len(ops) - 1:
        raise CompileError(f"{mapping.target_field}: cast must be the last step")

    declared = ops[-1].args["dtype"]
    if declared != field.dtype:
        raise CompileError(
            f"{mapping.target_field}: casts to {declared} but the target is {field.dtype}"
        )


def validate_spec(spec: MappingSpec, schema: TargetSchema) -> None:
    unknown = sorted({m.target_field for m in spec.mappings} - set(schema.field_names))
    if unknown:
        raise CompileError(f"spec references unknown target fields: {unknown}")

    missing = sorted(set(schema.field_names) - {m.target_field for m in spec.mappings})
    if missing:
        raise CompileError(f"spec is missing target fields: {missing}")

    for mapping in spec.mappings:
        validate(mapping, schema)


def required_columns(spec: MappingSpec) -> list[str]:
    return sorted({m.source_column for m in spec.mappings if m.source_column})
