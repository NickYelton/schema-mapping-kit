import pytest
from fastapi.testclient import TestClient

from app.core.settings import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def isolated_catalog(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "catalog.duckdb"))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(isolated_catalog):
    with TestClient(app) as c:
        yield c
