"""Phase 7: the endpoints behind the review UI."""

from app.models.mapping import OP_ARGS

GERMAN_STATUSES = {
    "retourniert": "returned",
    "offen": "pending",
    "storniert": "cancelled",
    "geliefert": "delivered",
    "versandt": "shipped",
}


def _proposed(client, name: str) -> tuple[str, dict]:
    source_id = client.post(f"/api/sources/from-sample?name={name}").json()["source_id"]
    client.post(f"/api/sources/{source_id}/propose?use_llm=false&use_embeddings=false")
    return source_id, client.get(f"/api/sources/{source_id}/spec").json()["spec"]


def _mapping(spec: dict, field: str) -> dict:
    return next(m for m in spec["mappings"] if m["target_field"] == field)


def _map_german_statuses(spec: dict) -> None:
    transforms = _mapping(spec, "status")["transforms"]
    transforms.insert(-1, {"op": "map_values", "args": {"mapping": GERMAN_STATUSES}})


def test_preview_reports_a_draft_without_saving_it(client):
    source_id, spec = _proposed(client, "orders_euro.xlsx")
    before = client.post(f"/api/sources/{source_id}/spec/preview", json=spec).json()
    assert (before["rows_valid"], before["rows_rejected"]) == (0, 30)
    assert before["field_problems"] == {"status": 30}

    _map_german_statuses(spec)
    after = client.post(f"/api/sources/{source_id}/spec/preview", json=spec).json()

    assert (after["rows_valid"], after["rows_rejected"]) == (30, 0)
    assert after["summary"] == "All 30 rows passed."
    assert after["content_hash"] != before["content_hash"]
    assert len(client.get(f"/api/sources/{source_id}/spec/history").json()) == 1


def test_preview_samples_pair_raw_and_transformed_values(client):
    source_id, spec = _proposed(client, "orders_messy.csv")
    body = client.post(f"/api/sources/{source_id}/spec/preview?samples=3", json=spec).json()

    assert len(body["samples"]["unit_price"]) == 3
    assert body["samples"]["unit_price"][0] == {"line": 6, "raw": "$0.85", "value": "0.85"}
    assert body["samples"]["customer_name"][0] == {"line": 6, "raw": None, "value": None}


def test_preview_rejects_an_uncompilable_draft(client):
    source_id, spec = _proposed(client, "orders_messy.csv")
    _mapping(spec, "quantity")["transforms"].pop()

    response = client.post(f"/api/sources/{source_id}/spec/preview", json=spec)
    assert response.status_code == 422
    assert "cast" in response.json()["detail"]


def test_preview_rejects_a_column_the_file_does_not_have(client):
    source_id, spec = _proposed(client, "orders_messy.csv")
    _mapping(spec, "sku")["source_column"] = "Not A Column"

    response = client.post(f"/api/sources/{source_id}/spec/preview", json=spec)
    assert response.status_code == 422
    assert "Not A Column" in response.json()["detail"]


def test_preview_rejects_a_spec_for_another_source(client):
    source_id, spec = _proposed(client, "orders_messy.csv")
    spec["source_id"] = "elsewhere"
    assert client.post(f"/api/sources/{source_id}/spec/preview", json=spec).status_code == 400


def test_suggest_derives_transforms_for_a_new_pairing(client):
    source_id, _ = _proposed(client, "orders_messy.csv")
    body = client.get(
        f"/api/sources/{source_id}/suggest",
        params={"column": "Price Each", "field": "unit_price"},
    ).json()

    ops = [t["op"] for t in body["transforms"]]
    assert "strip_currency" in ops
    assert body["transforms"][-1] == {"op": "cast", "args": {"dtype": "decimal"}}


def test_suggest_unknown_column_or_field_is_404(client):
    source_id, _ = _proposed(client, "orders_messy.csv")
    url = f"/api/sources/{source_id}/suggest"
    assert client.get(url, params={"column": "Nope", "field": "sku"}).status_code == 404
    assert client.get(url, params={"column": "Qty", "field": "nope"}).status_code == 404


def test_vocabulary_matches_the_compiled_op_set(client):
    body = client.get("/api/vocabulary").json()
    assert {o["op"] for o in body["ops"]} == set(OP_ARGS)
    assert {"format": "%d/%m/%Y", "label": "DD/MM/YYYY"} in body["date_formats"]
    assert body["decimal_locales"] == ["eu", "us"]


def test_history_describes_what_each_version_changed(client):
    source_id, spec = _proposed(client, "orders_euro.xlsx")
    _map_german_statuses(spec)
    assert client.put(f"/api/sources/{source_id}/spec", json=spec).status_code == 200

    history = client.get(f"/api/sources/{source_id}/spec/history").json()
    assert [h["version"] for h in history] == [2, 1]
    assert history[0]["changes"] == [
        {
            "target_field": "status",
            "before": "Termine_Status",
            "after": "Termine_Status",
            "transforms_changed": True,
        }
    ]
    assert history[1]["changes"] == []


def test_a_reviewed_correction_runs_clean(client):
    """The whole loop: propose, correct, save, run."""
    source_id, spec = _proposed(client, "orders_euro.xlsx")
    _map_german_statuses(spec)
    client.put(f"/api/sources/{source_id}/spec", json=spec)

    run = client.post(f"/api/sources/{source_id}/transform").json()
    assert (run["rows_valid"], run["rows_rejected"]) == (30, 0)


def test_preview_rejects_a_transform_that_cannot_run(client):
    source_id, spec = _proposed(client, "orders_messy.csv")
    _mapping(spec, "sku")["transforms"].insert(
        -1, {"op": "regex_extract", "args": {"pattern": "("}}
    )

    response = client.post(f"/api/sources/{source_id}/spec/preview", json=spec)
    assert response.status_code == 422
    assert response.json()["detail"].startswith("the transform failed")
