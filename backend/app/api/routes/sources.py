import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.core.settings import get_settings
from app.ingest import landing
from app.ingest.readers import UnsupportedFile, detect_kind, list_sheets

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
def index() -> list[dict]:
    return landing.list_sources()


@router.post("")
async def upload(
    file: UploadFile = File(...),
    sheet: str | None = Query(default=None),
) -> dict:
    name = Path(file.filename or "upload")
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / name.name
        with staged.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        try:
            return landing.register(staged, sheet=sheet)
        except UnsupportedFile as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc


@router.post("/from-sample")
def ingest_sample(name: str = Query(...), sheet: str | None = Query(default=None)) -> dict:
    """Register a bundled fixture. Keeps the demo runnable without a browser upload."""
    root = get_settings().samples.resolve()
    path = (root / name).resolve()
    if not path.is_file() or path.parent != root:
        raise HTTPException(status_code=404, detail=f"No sample named {name!r}")
    try:
        return landing.register(path, sheet=sheet)
    except UnsupportedFile as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc


@router.get("/samples")
def samples() -> list[dict]:
    root = get_settings().samples
    out = []
    for path in sorted(root.glob("*")):
        if path.suffix == ".py" or not path.is_file():
            continue
        try:
            kind = detect_kind(path)
        except UnsupportedFile:
            continue
        entry = {"name": path.name, "kind": kind, "bytes": path.stat().st_size}
        if kind == "excel":
            entry["sheets"] = list_sheets(path)
        out.append(entry)
    return out


@router.get("/{source_id}")
def detail(source_id: str) -> dict:
    source = landing.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}")
    return source


@router.get("/{source_id}/profile")
def profile(source_id: str) -> list[dict]:
    if landing.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}")
    return landing.get_profiles(source_id)


@router.get("/{source_id}/preview")
def preview(source_id: str, limit: int = Query(default=20, ge=1, le=200)) -> dict:
    if landing.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}")
    frame = landing.load_frame(source_id).head(limit)
    return {"columns": frame.columns, "rows": frame.rows()}
