import hashlib
import json
import uuid
from pathlib import Path

import polars as pl

from app.core.settings import get_settings
from app.db import duck
from app.ingest.readers import LandedFrame, land
from app.profile.profiler import profile_frame


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _parquet_path(source_id: str) -> Path:
    root = get_settings().artifacts / "landed"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{source_id}.parquet"


def register(path: Path, sheet: str | None = None) -> dict:
    """Land a file to Parquet, profile it, and record both in the catalog."""
    landed: LandedFrame = land(path, sheet=sheet)
    source_id = uuid.uuid4().hex[:12]
    parquet = _parquet_path(source_id)
    landed.frame.write_parquet(parquet)

    profiles = profile_frame(landed.frame)

    with duck.session() as conn:
        conn.execute(
            """
            INSERT INTO sources
                (id, filename, kind, sheet, landed_path, row_count, column_count,
                 fingerprint, sniff)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                source_id,
                path.name,
                landed.kind,
                landed.sheet,
                str(parquet),
                landed.frame.height,
                landed.frame.width - 1,
                fingerprint(path),
                json.dumps(landed.sniff),
            ],
        )
        for profile in profiles:
            conn.execute(
                "INSERT INTO column_profiles (source_id, column_name, ordinal, profile)"
                " VALUES (?, ?, ?, ?)",
                [source_id, profile["name"], profile["ordinal"], json.dumps(profile)],
            )

    return {
        "source_id": source_id,
        "filename": path.name,
        "kind": landed.kind,
        "sheet": landed.sheet,
        "row_count": landed.frame.height,
        "column_count": landed.frame.width - 1,
        "sniff": landed.sniff,
        "columns": [p["name"] for p in profiles],
    }


def landed_path(source_id: str) -> Path:
    with duck.session() as conn:
        row = conn.execute(
            "SELECT landed_path FROM sources WHERE id = ?", [source_id]
        ).fetchone()
    if row is None:
        raise KeyError(source_id)
    return Path(row[0])


def load_frame(source_id: str) -> pl.DataFrame:
    return pl.read_parquet(landed_path(source_id))


def get_source(source_id: str) -> dict | None:
    with duck.session() as conn:
        row = conn.execute(
            """
            SELECT id, filename, kind, sheet, row_count, column_count, fingerprint, sniff
            FROM sources WHERE id = ?
            """,
            [source_id],
        ).fetchone()
    if row is None:
        return None
    return {
        "source_id": row[0],
        "filename": row[1],
        "kind": row[2],
        "sheet": row[3],
        "row_count": row[4],
        "column_count": row[5],
        "fingerprint": row[6],
        "sniff": json.loads(row[7]) if row[7] else {},
    }


def list_sources() -> list[dict]:
    with duck.session() as conn:
        rows = conn.execute(
            """
            SELECT id, filename, kind, sheet, row_count, column_count, created_at
            FROM sources ORDER BY created_at DESC
            """
        ).fetchall()
    return [
        {
            "source_id": r[0],
            "filename": r[1],
            "kind": r[2],
            "sheet": r[3],
            "row_count": r[4],
            "column_count": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]


def get_profiles(source_id: str) -> list[dict]:
    with duck.session() as conn:
        rows = conn.execute(
            "SELECT profile FROM column_profiles WHERE source_id = ? ORDER BY ordinal",
            [source_id],
        ).fetchall()
    return [json.loads(r[0]) for r in rows]
