import io


def _register(client, name, **params):
    response = client.post("/api/sources/from-sample", params={"name": name, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_samples_are_listed_with_kinds_and_sheets(client):
    samples = {s["name"]: s for s in client.get("/api/sources/samples").json()}
    assert set(samples) == {
        "orders_clean.csv",
        "orders_messy.csv",
        "orders_euro.xlsx",
        "orders_nested.jsonl",
    }
    assert samples["orders_euro.xlsx"]["sheets"] == ["Deckblatt", "Bestellungen", "Hinweise"]
    assert samples["orders_nested.jsonl"]["kind"] == "json"


def test_register_lands_profiles_and_catalogs_a_source(client):
    body = _register(client, "orders_messy.csv")
    assert body["row_count"] == 30
    assert body["column_count"] == 12
    assert body["sniff"]["encoding"] == "cp1252"
    assert body["sniff"]["delimiter"] == ";"
    assert body["sniff"]["header_row"] == 4

    listed = client.get("/api/sources").json()
    assert [s["source_id"] for s in listed] == [body["source_id"]]


def test_profile_endpoint_returns_a_row_per_column(client):
    body = _register(client, "orders_clean.csv")
    profiles = client.get(f"/api/sources/{body['source_id']}/profile").json()
    assert len(profiles) == 12
    assert [p["ordinal"] for p in profiles] == list(range(12))
    assert {p["name"] for p in profiles} >= {"order_id", "status", "unit_price"}


def test_preview_returns_landed_rows_with_src_row_first(client):
    body = _register(client, "orders_messy.csv")
    preview = client.get(
        f"/api/sources/{body['source_id']}/preview", params={"limit": 3}
    ).json()
    assert preview["columns"][0] == "_src_row"
    assert len(preview["rows"]) == 3
    assert preview["rows"][0][0] == 6


def test_explicit_sheet_selection_overrides_the_auto_pick(client):
    auto = _register(client, "orders_euro.xlsx")
    assert auto["sheet"] == "Bestellungen"
    picked = _register(client, "orders_euro.xlsx", sheet="Hinweise")
    assert picked["sheet"] == "Hinweise"
    assert picked["row_count"] != auto["row_count"]


def test_upload_accepts_a_posted_file(client):
    payload = b"id,name\n1,alpha\n2,beta\n"
    response = client.post(
        "/api/sources",
        files={"file": ("tiny.csv", io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["row_count"] == 2
    assert body["columns"] == ["id", "name"]


def test_unsupported_upload_is_rejected_with_415(client):
    response = client.post(
        "/api/sources",
        files={"file": ("notes.docx", io.BytesIO(b"nope"), "application/octet-stream")},
    )
    assert response.status_code == 415


def test_unknown_source_returns_404(client):
    assert client.get("/api/sources/deadbeef").status_code == 404
    assert client.get("/api/sources/deadbeef/profile").status_code == 404


def test_sample_lookup_cannot_escape_the_samples_directory(client):
    response = client.post(
        "/api/sources/from-sample", params={"name": "../pyproject.toml"}
    )
    assert response.status_code == 404
