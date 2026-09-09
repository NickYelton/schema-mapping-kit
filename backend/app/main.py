from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, proposals, schemas, sources, transforms
from app.core.settings import get_settings
from app.db import duck


@asynccontextmanager
async def lifespan(app: FastAPI):
    duck.init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Schema Mapping & Onboarding Kit",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(sources.router, prefix="/api")
    app.include_router(schemas.router, prefix="/api")
    app.include_router(proposals.router, prefix="/api")
    app.include_router(transforms.router, prefix="/api")
    return app


app = create_app()
