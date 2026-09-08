"""Pydantic models for a canonical target schema.

These are the in-memory form of a `schemas/*.yaml` file. Every later phase reads them:
the proposer embeds `description` and fuzzy-matches `aliases`, the transform compiler
reads `dtype` to pick a cast, and the validator turns `constraints` into Pandera checks.
"""

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

DType = Literal["string", "integer", "decimal", "boolean", "date", "datetime"]

NUMERIC_DTYPES = {"integer", "decimal"}


class Constraints(BaseModel):
    """The closed vocabulary of field constraints.

    Closed on purpose: each entry has to compile to a Pandera check in `app.target.pandera`
    and, later, to a rejection message a customer can act on. An open-ended predicate would
    compile to neither.
    """

    model_config = ConfigDict(extra="forbid")

    enum: list[str] | None = None
    pattern: str | None = None
    min: float | None = None
    max: float | None = None
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=0)
    unique: bool = False

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min ({self.min}) exceeds max ({self.max})")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError(
                f"min_length ({self.min_length}) exceeds max_length ({self.max_length})"
            )
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"pattern is not a valid regex: {exc}") from exc
        return self


class TargetField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    dtype: DType
    nullable: bool = True
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    examples: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_applicability(self) -> Self:
        """Reject constraints that cannot apply to the declared dtype.

        Catching `min: 0` on a string column here — at load time, with the field name in
        the message — is far kinder than letting Pandera raise on a type mismatch during a
        run against customer data.
        """
        c = self.constraints
        if c.min is not None or c.max is not None:
            if self.dtype not in NUMERIC_DTYPES:
                raise ValueError(f"{self.name}: min/max require a numeric dtype, got {self.dtype}")
        if c.pattern is not None or c.min_length is not None or c.max_length is not None:
            if self.dtype != "string":
                raise ValueError(
                    f"{self.name}: pattern/length constraints require dtype string,"
                    f" got {self.dtype}"
                )
        if c.enum is not None:
            if not c.enum:
                raise ValueError(f"{self.name}: enum must list at least one value")
            if len(set(c.enum)) != len(c.enum):
                raise ValueError(f"{self.name}: enum contains duplicate values")
        return self

    def normalized_aliases(self) -> set[str]:
        """Alias set for matching, including the field's own name. Lower-cased, spaces and
        punctuation collapsed to underscores so `Price Each` and `price_each` unify."""
        return {_normalize(a) for a in [self.name, *self.aliases]}

    def embedding_text(self) -> str:
        """The text the proposer embeds to match this field by meaning."""
        parts = [self.name.replace("_", " "), self.description.strip()]
        if self.constraints.enum:
            parts.append("one of: " + ", ".join(self.constraints.enum))
        elif self.examples:
            parts.append("for example: " + ", ".join(self.examples[:3]))
        return ". ".join(p for p in parts if p)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


class TargetSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: int = Field(ge=1)
    title: str = ""
    description: str = ""
    primary_key: list[str] = Field(default_factory=list)
    fields: list[TargetField]

    @model_validator(mode="after")
    def _check_coherence(self) -> Self:
        if not self.fields:
            raise ValueError(f"{self.name}: schema declares no fields")
        names = [f.name for f in self.fields]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"{self.name}: duplicate field names {sorted(duplicates)}")
        missing = [k for k in self.primary_key if k not in names]
        if missing:
            raise ValueError(f"{self.name}: primary_key references unknown fields {missing}")
        nullable_keys = [f.name for f in self.fields if f.name in self.primary_key and f.nullable]
        if nullable_keys:
            raise ValueError(
                f"{self.name}: primary_key fields must not be nullable: {nullable_keys}"
            )
        return self

    @property
    def slug(self) -> str:
        """Stable identifier pairing name and version, e.g. `orders_v1`."""
        return f"{self.name}_v{self.version}"

    def field(self, name: str) -> TargetField:
        for candidate in self.fields:
            if candidate.name == name:
                return candidate
        raise KeyError(f"{self.name} has no field {name!r}")

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]
