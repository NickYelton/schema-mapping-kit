from fastapi import APIRouter

from app.core.settings import get_settings
from app.db import duck

router = APIRouter(tags=["health"])

# Bumped as each phase lands, so the UI badge has one source of truth rather than a
# hard-coded string that silently describes the phase before last.
PHASE = 7
PHASE_LABEL = "review"
PHASE_TOTAL = 7


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    with duck.session() as conn:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
    return {
        "status": "ok",
        "phase": PHASE,
        "phase_label": PHASE_LABEL,
        "phase_total": PHASE_TOTAL,
        "llm_provider": settings.llm_provider,
        "catalog": str(settings.duckdb_file),
        "tables": sorted(tables),
    }
