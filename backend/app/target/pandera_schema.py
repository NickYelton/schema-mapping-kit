"""Compile a TargetSchema into a Pandera schema for the Polars backend.

This is the validation half of the target phase: the same YAML that tells the proposer what
to aim at tells the validator what to reject. Keeping one source for both is the point —
a mapping cannot be accepted against constraints the run does not then enforce.
"""

import pandera.polars as pa
import polars as pl

from app.models.target import Constraints, TargetField, TargetSchema

POLARS_DTYPES: dict[str, pl.DataType] = {
    "string": pl.Utf8,
    "integer": pl.Int64,
    # Float64 rather than pl.Decimal: Polars' decimal support is still unstable, and the
    # money in these files is well inside the range where float64 is exact enough. Revisit
    # if a target schema ever needs exact fixed-point arithmetic.
    "decimal": pl.Float64,
    "boolean": pl.Boolean,
    "date": pl.Date,
    "datetime": pl.Datetime,
}


def polars_dtype(dtype: str) -> pl.DataType:
    try:
        return POLARS_DTYPES[dtype]
    except KeyError as exc:
        raise ValueError(f"No Polars dtype mapped for {dtype!r}") from exc


def _checks(constraints: Constraints) -> list[pa.Check]:
    checks: list[pa.Check] = []
    if constraints.enum is not None:
        checks.append(pa.Check.isin(constraints.enum))
    if constraints.pattern is not None:
        checks.append(pa.Check.str_matches(constraints.pattern))
    if constraints.min is not None:
        checks.append(pa.Check.ge(constraints.min))
    if constraints.max is not None:
        checks.append(pa.Check.le(constraints.max))
    if constraints.min_length is not None or constraints.max_length is not None:
        checks.append(
            pa.Check.str_length(min_value=constraints.min_length, max_value=constraints.max_length)
        )
    return checks


def column(field: TargetField) -> pa.Column:
    return pa.Column(
        polars_dtype(field.dtype),
        checks=_checks(field.constraints),
        nullable=field.nullable,
        unique=field.constraints.unique,
        required=True,
        description=field.description.strip() or None,
    )


def build(schema: TargetSchema, *, strict: bool = True, coerce: bool = False) -> pa.DataFrameSchema:
    """Build the Pandera schema.

    `strict` rejects columns the target does not declare, which is what a compiled transform
    should produce. `coerce` is off by default because the transform is responsible for
    casting: letting Pandera coerce would paper over a mapping that emits the wrong type.
    """
    return pa.DataFrameSchema(
        columns={f.name: column(f) for f in schema.fields},
        # A composite primary key is a frame-level uniqueness constraint, not a per-column
        # one: order_id repeats across the lines of an order and only the pair is unique.
        unique=list(schema.primary_key) or None,
        strict=strict,
        coerce=coerce,
        ordered=False,
        name=schema.slug,
        title=schema.title or None,
        description=schema.description.strip() or None,
    )


def empty_frame(schema: TargetSchema) -> pl.DataFrame:
    """An empty frame with the target's exact column names and dtypes.

    Useful as the skeleton a transform fills, and as a cheap check that the dtype mapping
    round-trips through Polars.
    """
    return pl.DataFrame(schema={f.name: polars_dtype(f.dtype) for f in schema.fields})
