from fastapi import APIRouter

from app.core.settings import get_settings
from app.db import duck

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    with duck.session() as conn:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "catalog": str(settings.duckdb_file),
        "tables": sorted(tables),
    }
