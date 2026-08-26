EXPECTED_TABLES = {
    "column_profiles",
    "embedding_cache",
    "llm_calls",
    "mapping_specs",
    "runs",
    "sources",
}


def test_health_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_catalog_schema_created_on_startup(client):
    tables = set(client.get("/api/health").json()["tables"])
    assert EXPECTED_TABLES <= tables
