"""Persistence for MappingSpecs.

Versioning is content-addressed: saving a spec whose meaning matches the current head is a
no-op that returns the existing row. Only a real change — a different source column, a
different transform — creates a new version, linked to its parent. That keeps the history a
record of decisions rather than of save-button presses.
"""

import json
import uuid

from app.db import duck
from app.models.mapping import MappingSpec


def _row_to_dict(row: tuple) -> dict:
    spec = MappingSpec.model_validate_json(row[6])
    return {
        "id": row[0],
        "source_id": row[1],
        "target_schema": row[2],
        "target_version": row[3],
        "version": row[4],
        "parent_id": row[5],
        "content_hash": row[7],
        "created_by": row[8],
        "created_at": row[9].isoformat() if row[9] else None,
        "spec": spec.model_dump(mode="json"),
    }


SELECT = """
    SELECT id, source_id, target_schema, target_version, version, parent_id, spec,
           content_hash, created_by, created_at
    FROM mapping_specs
"""


def head(source_id: str) -> dict | None:
    """The newest spec for a source, or None."""
    with duck.session() as conn:
        row = conn.execute(
            SELECT + " WHERE source_id = ? ORDER BY version DESC LIMIT 1", [source_id]
        ).fetchone()
    return _row_to_dict(row) if row else None


def get(spec_id: str) -> dict | None:
    with duck.session() as conn:
        row = conn.execute(SELECT + " WHERE id = ?", [spec_id]).fetchone()
    return _row_to_dict(row) if row else None


def history(source_id: str) -> list[dict]:
    with duck.session() as conn:
        rows = conn.execute(
            SELECT + " WHERE source_id = ? ORDER BY version DESC", [source_id]
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def save(spec: MappingSpec, created_by: str | None = None) -> dict:
    """Persist a spec as a new version, or return the existing one if nothing changed."""
    current = head(spec.source_id)
    digest = spec.content_hash()

    if current is not None and current["content_hash"] == digest:
        return current

    version = 1 if current is None else current["version"] + 1
    parent_id = None if current is None else current["id"]
    spec_id = uuid.uuid4().hex[:12]

    stored = spec.model_copy(update={"version": version, "parent_id": parent_id})

    with duck.session() as conn:
        conn.execute(
            """
            INSERT INTO mapping_specs
                (id, source_id, target_schema, target_version, version, parent_id,
                 content_hash, spec, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                spec_id,
                stored.source_id,
                stored.target_schema,
                stored.target_version,
                version,
                parent_id,
                digest,
                stored.model_dump_json(),
                created_by,
            ],
        )

    saved = get(spec_id)
    assert saved is not None
    return saved


def diff(older: MappingSpec, newer: MappingSpec) -> list[dict]:
    """Per-field changes between two specs, for a review UI to render."""
    changes: list[dict] = []
    fields = {m.target_field for m in older.mappings} | {m.target_field for m in newer.mappings}
    for name in sorted(fields):
        try:
            before = older.mapping(name)
        except KeyError:
            before = None
        try:
            after = newer.mapping(name)
        except KeyError:
            after = None

        before_src = before.source_column if before else None
        after_src = after.source_column if after else None
        before_ops = json.dumps([t.model_dump() for t in before.transforms]) if before else "[]"
        after_ops = json.dumps([t.model_dump() for t in after.transforms]) if after else "[]"

        if before_src != after_src or before_ops != after_ops:
            changes.append(
                {
                    "target_field": name,
                    "before": before_src,
                    "after": after_src,
                    "transforms_changed": before_ops != after_ops,
                }
            )
    return changes
