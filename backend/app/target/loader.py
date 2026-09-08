"""Load canonical target schemas from `schemas/*.yaml`."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.core.settings import get_settings
from app.models.target import TargetSchema

SUFFIXES = {".yaml", ".yml"}


class SchemaError(ValueError):
    """A schema file is missing, malformed, or internally inconsistent."""


def schema_paths() -> list[Path]:
    root = get_settings().schemas
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.suffix.lower() in SUFFIXES and p.is_file())


def load_path(path: Path) -> TargetSchema:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SchemaError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SchemaError(f"{path.name}: expected a mapping at the top level")
    try:
        schema = TargetSchema.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"{path.name}: {exc}") from exc

    # The filename carries the version too; a mismatch means one of them was edited alone,
    # and silently trusting either would version a mapping spec against the wrong schema.
    expected = f"{schema.slug}{path.suffix}"
    if path.name != expected:
        raise SchemaError(
            f"{path.name}: declares name={schema.name!r} version={schema.version},"
            f" so the file should be named {expected}"
        )
    return schema


@lru_cache
def _load_cached(fingerprint: tuple[tuple[str, float], ...]) -> dict[str, TargetSchema]:
    schemas: dict[str, TargetSchema] = {}
    for name, _ in fingerprint:
        schema = load_path(Path(name))
        schemas[schema.slug] = schema
    return schemas


def load_all() -> dict[str, TargetSchema]:
    """Every schema on disk, keyed by slug (`orders_v1`).

    Cached on the files' paths and mtimes rather than unconditionally, so editing a schema
    under `uvicorn --reload` takes effect without a restart and tests can point
    SCHEMAS_DIR at a temp directory without fighting a stale cache.
    """
    fingerprint = tuple((str(p), p.stat().st_mtime) for p in schema_paths())
    return _load_cached(fingerprint)


def get(name: str, version: int | None = None) -> TargetSchema:
    """Fetch a schema by name, defaulting to its highest version.

    Accepts a bare name (`orders`) or a slug (`orders_v1`); mapping specs store the name and
    version separately, while the API takes whichever the caller has.
    """
    schemas = load_all()
    if version is None and name in schemas:
        return schemas[name]

    if version is None:
        matches = [s for s in schemas.values() if s.name == name]
        if not matches:
            raise KeyError(name)
        return max(matches, key=lambda s: s.version)

    slug = f"{name}_v{version}"
    if slug not in schemas:
        raise KeyError(slug)
    return schemas[slug]


def versions(name: str) -> list[int]:
    return sorted(s.version for s in load_all().values() if s.name == name)
