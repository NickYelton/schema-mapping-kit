"""The MappingSpec: a reviewed, versioned description of how one source file becomes the
canonical target.

This is the artifact the whole tool exists to produce. The proposer drafts it, a human
corrects it, and the transform compiler turns it into SQL and Polars. It has to be complete
enough that nothing downstream needs to consult the LLM again, and small enough that a
person can read it in a review UI.
"""

import hashlib
import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Provider = Literal["heuristic", "embedding", "llm", "human"]

OpName = Literal[
    "strip",
    "lower",
    "upper",
    "null_if",
    "strip_currency",
    "parse_decimal",
    "parse_percent",
    "parse_date",
    "map_values",
    "regex_extract",
    "default",
    "cast",
]

# Required and optional args per op. The transform compiler in phase 5 implements this exact
# table for both SQL and Polars, so an op that is not here cannot be compiled and must not be
# proposable. Keeping the vocabulary closed is what makes the compiled output deterministic.
OP_ARGS: dict[str, tuple[set[str], set[str]]] = {
    "strip": (set(), set()),
    "lower": (set(), set()),
    "upper": (set(), set()),
    "null_if": ({"tokens"}, set()),
    "strip_currency": (set(), set()),
    "parse_decimal": (set(), {"locale"}),
    "parse_percent": (set(), set()),
    "parse_date": ({"format"}, set()),
    "map_values": ({"mapping"}, {"default"}),
    "regex_extract": ({"pattern"}, {"group"}),
    "default": ({"value"}, set()),
    "cast": ({"dtype"}, set()),
}

DECIMAL_LOCALES = {"us", "eu"}


class TransformOp(BaseModel):
    """One step in a column's transform pipeline, applied in order."""

    model_config = ConfigDict(extra="forbid")

    op: OpName
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_args(self) -> Self:
        required, optional = OP_ARGS[self.op]
        supplied = set(self.args)
        if missing := required - supplied:
            raise ValueError(f"{self.op}: missing required args {sorted(missing)}")
        if unknown := supplied - required - optional:
            raise ValueError(f"{self.op}: unexpected args {sorted(unknown)}")

        if self.op == "parse_decimal":
            locale = self.args.get("locale", "us")
            if locale not in DECIMAL_LOCALES:
                raise ValueError(f"parse_decimal: locale must be one of {sorted(DECIMAL_LOCALES)}")
        if self.op == "map_values" and not isinstance(self.args["mapping"], dict):
            raise ValueError("map_values: mapping must be an object")
        if self.op == "null_if" and not isinstance(self.args["tokens"], list):
            raise ValueError("null_if: tokens must be a list")
        return self

    def describe(self) -> str:
        """A phrase a reviewer can read, e.g. `parse dates as DD/MM/YYYY`."""
        match self.op:
            case "strip":
                return "trim surrounding whitespace"
            case "lower":
                return "lower-case"
            case "upper":
                return "upper-case"
            case "null_if":
                return f"treat {', '.join(map(repr, self.args['tokens']))} as null"
            case "strip_currency":
                return "remove the currency symbol and thousands separators"
            case "parse_decimal":
                style = "1.234,56" if self.args.get("locale", "us") == "eu" else "1,234.56"
                return f"read decimals written as {style}"
            case "parse_percent":
                return "drop the percent sign, keeping a number out of 100"
            case "parse_date":
                return f"parse dates as {self.args['format']}"
            case "map_values":
                pairs = list(self.args["mapping"].items())[:3]
                shown = ", ".join(f"{k}->{v}" for k, v in pairs)
                more = "" if len(self.args["mapping"]) <= 3 else ", …"
                return f"map values ({shown}{more})"
            case "regex_extract":
                return f"extract with {self.args['pattern']}"
            case "default":
                return f"fill nulls with {self.args['value']!r}"
            case "cast":
                return f"cast to {self.args['dtype']}"
        return self.op


class Evidence(BaseModel):
    """Why one proposer believed a match. Carried through to the review UI verbatim:
    a reviewer overruling a suggestion deserves to see what argued for it."""

    model_config = ConfigDict(extra="forbid")

    provider: Provider
    score: float = Field(ge=0.0, le=1.0)
    detail: str = ""


class Candidate(BaseModel):
    """A ranked possibility for one source column, before a decision is made."""

    model_config = ConfigDict(extra="forbid")

    target_field: str
    score: float = Field(ge=0.0, le=1.0)
    transforms: list[TransformOp] = Field(default_factory=list)
    provenance: list[Evidence] = Field(default_factory=list)

    @property
    def agreement(self) -> int:
        """How many distinct providers backed this candidate."""
        return len({e.provider for e in self.provenance})


class FieldMapping(BaseModel):
    """How one target field gets filled."""

    model_config = ConfigDict(extra="forbid")

    target_field: str
    source_column: str | None = None
    literal: str | None = None
    transforms: list[TransformOp] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provenance: list[Evidence] = Field(default_factory=list)
    decided_by: Literal["proposed", "human"] = "proposed"
    note: str = ""
    alternatives: list[Candidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_source(self) -> Self:
        if self.source_column is not None and self.literal is not None:
            raise ValueError(f"{self.target_field}: set source_column or literal, not both")
        return self

    @property
    def is_mapped(self) -> bool:
        return self.source_column is not None or self.literal is not None


class MappingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    target_schema: str
    target_version: int = Field(ge=1)
    mappings: list[FieldMapping]
    unmapped_columns: list[str] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    parent_id: str | None = None
    created_by: str | None = None

    @model_validator(mode="after")
    def _check_unique_targets(self) -> Self:
        names = [m.target_field for m in self.mappings]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate target fields in spec: {sorted(duplicates)}")
        return self

    def mapping(self, target_field: str) -> FieldMapping:
        for m in self.mappings:
            if m.target_field == target_field:
                return m
        raise KeyError(target_field)

    @property
    def mapped_count(self) -> int:
        return sum(1 for m in self.mappings if m.is_mapped)

    def content_hash(self) -> str:
        """Hash of the spec's meaning, ignoring lineage and authorship.

        Two specs that would compile to the same transform hash the same, so re-saving an
        unchanged review is a no-op rather than a new version. Provenance and confidence are
        excluded deliberately: re-running the proposer with a different model must not
        invent a new version when the human-visible decisions are identical.
        """
        payload = {
            "source_id": self.source_id,
            "target_schema": self.target_schema,
            "target_version": self.target_version,
            "mappings": [
                {
                    "target_field": m.target_field,
                    "source_column": m.source_column,
                    "literal": m.literal,
                    "transforms": [{"op": t.op, "args": t.args} for t in m.transforms],
                }
                for m in sorted(self.mappings, key=lambda m: m.target_field)
            ],
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
