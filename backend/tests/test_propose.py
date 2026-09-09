"""Phase 4: the proposer.

The accuracy tests below assert against a hand-labelled expected mapping for every column of
every fixture. That table is the real specification of this phase — the scores and weights
are implementation detail, but "the German sheet's Artikelnr is the sku" is not.
"""

import json
from pathlib import Path

import pytest

from app.core.settings import get_settings
from app.ingest.readers import land
from app.models.mapping import (
    Candidate,
    Evidence,
    FieldMapping,
    MappingSpec,
    TransformOp,
)
from app.profile.profiler import profile_frame
from app.propose import ensemble, store
from app.propose.providers import embeddings, heuristic, llm
from app.target import loader

SAMPLES = Path(__file__).resolve().parents[2] / "samples"

# The correct answer for every column in every fixture.
EXPECTED: dict[str, dict[str, str]] = {
    "orders_clean.csv": {
        c: c
        for c in [
            "order_id",
            "line_number",
            "customer_id",
            "sku",
            "product_name",
            "quantity",
            "unit_price",
            "currency",
            "discount_pct",
            "order_date",
            "ship_date",
            "status",
        ]
    },
    "orders_messy.csv": {
        "Ord #": "order_id",
        "Ln": "line_number",
        "Cust": "customer_id",
        "Item SKU": "sku",
        "Description": "product_name",
        "Qty": "quantity",
        "Price Each": "unit_price",
        "Curr": "currency",
        "Disc %": "discount_pct",
        "Ordered On": "order_date",
        "Shipped": "ship_date",
        "X7": "status",
    },
    "orders_euro.xlsx": {
        "Bestellung_Bestellnr": "order_id",
        "Bestellung_Pos": "line_number",
        "Bestellung_Kundennr": "customer_id",
        "Artikel_Artikelnr": "sku",
        "Artikel_Bezeichnung": "product_name",
        "Artikel_Menge": "quantity",
        "Betrag_Einzelpreis": "unit_price",
        "Betrag_Waehrung": "currency",
        "Betrag_Rabatt": "discount_pct",
        "Termine_Bestelldatum": "order_date",
        "Termine_Lieferdatum": "ship_date",
        "Termine_Status": "status",
    },
    "orders_nested.jsonl": {
        "orderId": "order_id",
        "line": "line_number",
        "customer.id": "customer_id",
        "customer.name": "customer_name",
        "item.sku": "sku",
        "item.name": "product_name",
        "price.amount": "unit_price",
        "price.currency": "currency",
        "discountPct": "discount_pct",
        "orderedAt": "order_date",
        "shippedAt": "ship_date",
        "state": "status",
        "quantity": "quantity",
        "qty": "quantity",
    },
}


@pytest.fixture(scope="module")
def orders():
    return loader.get("orders")


def profiles_for(name: str) -> list[dict]:
    return profile_frame(land(SAMPLES / name).frame)


# ---------------------------------------------------------------- heuristic accuracy


@pytest.mark.parametrize("sample", sorted(EXPECTED))
def test_heuristic_maps_every_column_correctly(sample, orders):
    """The heuristic alone must get every column of every fixture right.

    It is the only provider guaranteed to be present (no model download, no API key), so the
    offline floor for the whole tool is exactly this provider's accuracy.
    """
    expected = EXPECTED[sample]
    wrong = []
    for profile in profiles_for(sample):
        want = expected.get(profile["name"])
        if want is None:
            continue
        candidates = heuristic.propose_column(profile, orders)
        got = candidates[0].target_field if candidates else None
        if got != want:
            wrong.append(f"{profile['name']}: got {got}, want {want}")
    assert not wrong, "\n".join(wrong)


def test_meaningless_column_name_is_matched_by_its_values(orders):
    """`X7` in orders_messy.csv is the status column and nothing in its name says so."""
    profile = next(p for p in profiles_for("orders_messy.csv") if p["name"] == "X7")
    best = heuristic.propose_column(profile, orders)[0]
    assert best.target_field == "status"
    assert any("enum" in e.detail for e in best.provenance)


def test_merged_header_prefix_does_not_defeat_matching(orders):
    """`Bestellung_Kundennr` is the customer id, not the order id its prefix suggests."""
    profile = next(
        p for p in profiles_for("orders_euro.xlsx") if p["name"] == "Bestellung_Kundennr"
    )
    assert heuristic.propose_column(profile, orders)[0].target_field == "customer_id"


def test_name_variants_include_trailing_segment():
    assert heuristic.name_variants("Bestellung_Kundennr") == [
        "bestellung_kundennr",
        "kundennr",
    ]
    assert heuristic.name_variants("customer.id") == ["customer_id", "id"]
    assert heuristic.name_variants("sku") == ["sku"]


def test_exact_alias_hit_is_reported_as_exact(orders):
    score, alias, exact = heuristic.name_score("Price Each", orders.field("unit_price"))
    assert exact and alias == "price_each"
    # Exact, but on an alias rather than the field's own name, so just short of 1.0.
    assert score == 0.98


def test_field_name_outranks_an_abbreviation_alias(orders):
    """Both hit `quantity` exactly; the column spelled like the field must score higher."""
    exactly, _, _ = heuristic.name_score("quantity", orders.field("quantity"))
    abbreviated, _, _ = heuristic.name_score("qty", orders.field("quantity"))
    assert exactly > abbreviated == 0.98


# ---------------------------------------------------------------- transform suggestions


def test_currency_and_percent_transforms_come_from_the_profile(orders):
    profiles = {p["name"]: p for p in profiles_for("orders_messy.csv")}
    price_ops = [
        t.op
        for t in heuristic.suggest_transforms(profiles["Price Each"], orders.field("unit_price"))
    ]
    assert "strip_currency" in price_ops
    assert price_ops[-1] == "cast"

    disc_ops = [
        t.op for t in heuristic.suggest_transforms(profiles["Disc %"], orders.field("discount_pct"))
    ]
    assert "parse_percent" in disc_ops


def test_european_decimals_are_detected(orders):
    profiles = {p["name"]: p for p in profiles_for("orders_euro.xlsx")}
    ops = heuristic.suggest_transforms(profiles["Betrag_Einzelpreis"], orders.field("unit_price"))
    parse = next(t for t in ops if t.op == "parse_decimal")
    assert parse.args["locale"] == "eu"


def test_date_format_is_disambiguated_per_file(orders):
    """The same calendar dates are DD/MM in the German file and MM/DD in the US one."""
    euro = {p["name"]: p for p in profiles_for("orders_euro.xlsx")}
    messy = {p["name"]: p for p in profiles_for("orders_messy.csv")}

    euro_fmt = next(
        t
        for t in heuristic.suggest_transforms(
            euro["Termine_Bestelldatum"], orders.field("order_date")
        )
        if t.op == "parse_date"
    ).args["format"]
    messy_fmt = next(
        t
        for t in heuristic.suggest_transforms(messy["Ordered On"], orders.field("order_date"))
        if t.op == "parse_date"
    ).args["format"]

    assert euro_fmt == "%d/%m/%Y"
    assert messy_fmt == "%m/%d/%Y"


def test_case_only_enum_differences_are_mapped(orders):
    profile = next(p for p in profiles_for("orders_messy.csv") if p["name"] == "X7")
    ops = heuristic.suggest_transforms(profile, orders.field("status"))
    mapping = next(t for t in ops if t.op == "map_values").args["mapping"]
    assert mapping["RETURNED"] == "returned"
    assert all(v in orders.field("status").constraints.enum for v in mapping.values())


def test_foreign_enum_values_are_left_for_a_human(orders):
    """`retourniert` is not guessed into `returned` — a wrong guess here is invisible."""
    profile = next(p for p in profiles_for("orders_euro.xlsx") if p["name"] == "Termine_Status")
    ops = heuristic.suggest_transforms(profile, orders.field("status"))
    mappings = [t for t in ops if t.op == "map_values"]
    assert not mappings or "retourniert" not in mappings[0].args["mapping"]


# ---------------------------------------------------------------- transform op model


def test_transform_op_requires_its_args():
    with pytest.raises(ValueError, match="missing required args"):
        TransformOp(op="parse_date")
    with pytest.raises(ValueError, match="unexpected args"):
        TransformOp(op="strip", args={"nope": 1})
    with pytest.raises(ValueError, match="locale must be one of"):
        TransformOp(op="parse_decimal", args={"locale": "martian"})
    with pytest.raises(ValueError, match="mapping must be an object"):
        TransformOp(op="map_values", args={"mapping": ["a", "b"]})


def test_transform_op_describes_itself_in_english():
    assert "DD" in TransformOp(op="parse_date", args={"format": "DD/MM/YYYY"}).describe()
    assert "1.234,56" in TransformOp(op="parse_decimal", args={"locale": "eu"}).describe()
    assert TransformOp(op="strip").describe() == "trim surrounding whitespace"


def test_unknown_op_is_rejected():
    with pytest.raises(ValueError):
        TransformOp(op="teleport")


# ---------------------------------------------------------------- ensemble


def test_ensemble_maps_every_field_without_llm_or_embeddings(orders):
    """The offline floor: heuristics alone fill every field the fixture can supply."""
    for sample, expected in EXPECTED.items():
        profiles = profiles_for(sample)
        spec = ensemble.propose(
            profiles, orders, source_id="s1", use_llm=False, use_embeddings=False
        )
        wanted = set(expected.values())
        for mapping in spec.mappings:
            if mapping.target_field not in wanted:
                continue
            assert mapping.source_column is not None, (
                f"{sample}: {mapping.target_field} was left unmapped"
            )
            assert expected[mapping.source_column] == mapping.target_field, (
                f"{sample}: {mapping.target_field} <- {mapping.source_column}"
            )


def test_competing_columns_resolve_to_one_field(orders):
    """orders_nested.jsonl carries both `quantity` and a drifted `qty`."""
    profiles = profiles_for("orders_nested.jsonl")
    spec = ensemble.propose(profiles, orders, source_id="s1", use_llm=False, use_embeddings=False)
    assert spec.mapping("quantity").source_column == "quantity"
    assert "qty" in spec.unmapped_columns


def test_absent_target_field_is_reported_not_invented(orders):
    """orders_messy.csv has no customer name column, so that field stays empty."""
    spec = ensemble.propose(
        profiles_for("orders_messy.csv"),
        orders,
        source_id="s1",
        use_llm=False,
        use_embeddings=False,
    )
    mapping = spec.mapping("customer_name")
    assert mapping.source_column is None
    assert not mapping.is_mapped
    assert mapping.confidence == 0.0


def test_provenance_is_carried_through(orders):
    spec = ensemble.propose(
        profiles_for("orders_clean.csv"),
        orders,
        source_id="s1",
        use_llm=False,
        use_embeddings=False,
    )
    mapping = spec.mapping("order_id")
    assert mapping.provenance
    assert {e.provider for e in mapping.provenance} == {"heuristic"}
    assert "heuristic" in ensemble.evidence_summary(mapping)


def test_missing_providers_do_not_deflate_confidence(orders):
    """Heuristic-only confidence must not be scaled down by absent voices."""
    spec = ensemble.propose(
        profiles_for("orders_clean.csv"),
        orders,
        source_id="s1",
        use_llm=False,
        use_embeddings=False,
    )
    assert spec.mapping("order_id").confidence > 0.8


def test_agreement_between_providers_raises_confidence():
    """Two providers naming the same field must beat either one alone.

    Built from synthetic candidates rather than a real fixture so the scores sit well below
    the 1.0 cap, where an agreement bonus would be invisible.
    """

    def candidate(provider: str, score: float) -> Candidate:
        return Candidate(
            target_field="sku",
            score=score,
            provenance=[Evidence(provider=provider, score=score, detail=provider)],
        )

    alone = ensemble._merge({"heuristic": {"col": [candidate("heuristic", 0.5)]}}, ["col"])
    together = ensemble._merge(
        {
            "heuristic": {"col": [candidate("heuristic", 0.5)]},
            "llm": {"col": [candidate("llm", 0.5)]},
        },
        ["col"],
    )
    assert together["col"][0].score > alone["col"][0].score
    assert {e.provider for e in together["col"][0].provenance} == {"heuristic", "llm"}


# ---------------------------------------------------------------- LLM provider


def test_request_hash_is_stable_and_content_addressed(orders):
    profiles = profiles_for("orders_clean.csv")
    a = llm.build_request(profiles, orders)
    b = llm.build_request(profiles, orders)
    assert llm.request_hash(a) == llm.request_hash(b)

    changed = json.loads(json.dumps(a))
    changed["columns"][0]["name"] = "renamed"
    assert llm.request_hash(changed) != llm.request_hash(a)


def test_replay_serves_a_recorded_fixture(orders, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FIXTURES_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    get_settings.cache_clear()

    profiles = profiles_for("orders_clean.csv")
    request = llm.build_request(profiles, orders)
    digest = llm.request_hash(request)
    llm.save_fixture(
        digest,
        request,
        {
            "mappings": [
                {
                    "source_column": "order_id",
                    "target_field": "order_id",
                    "confidence": 0.95,
                    "reasoning": "identifier shaped like an order number",
                }
            ]
        },
        "synthetic",
        "test-double",
    )

    result = llm.propose(profiles, orders, source_id="s1")
    assert result["order_id"][0].target_field == "order_id"
    assert result["order_id"][0].provenance[0].provider == "llm"


def test_replay_without_a_fixture_degrades_quietly(orders, tmp_path, monkeypatch):
    """A file nobody recorded must fall back to the other providers, not 500."""
    monkeypatch.setenv("LLM_FIXTURES_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    get_settings.cache_clear()
    assert llm.propose(profiles_for("orders_clean.csv"), orders, source_id="s1") == {}


def test_llm_naming_an_unknown_target_field_is_dropped(orders, tmp_path, monkeypatch):
    """The model inventing `total_price` must not reach the spec."""
    monkeypatch.setenv("LLM_FIXTURES_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    get_settings.cache_clear()

    profiles = profiles_for("orders_clean.csv")
    request = llm.build_request(profiles, orders)
    llm.save_fixture(
        llm.request_hash(request),
        request,
        {
            "mappings": [
                {
                    "source_column": "order_id",
                    "target_field": "total_price",
                    "confidence": 0.9,
                    "reasoning": "hallucinated",
                },
                {
                    "source_column": "sku",
                    "target_field": "sku",
                    "confidence": 0.9,
                    "reasoning": "fine",
                },
            ]
        },
        "synthetic",
        "test-double",
    )
    result = llm.propose(profiles, orders, source_id="s1")
    assert "order_id" not in result
    assert result["sku"][0].target_field == "sku"


def test_malformed_fixture_is_ignored(orders, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FIXTURES_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    get_settings.cache_clear()
    profiles = profiles_for("orders_clean.csv")
    digest = llm.request_hash(llm.build_request(profiles, orders))
    (tmp_path / f"{digest}.json").write_text("{not json", encoding="utf-8")
    assert llm.propose(profiles, orders, source_id="s1") == {}


def test_request_excludes_volatile_counts(orders):
    """Row counts must not be in the hash, or an updated extract misses its fixture."""
    request = llm.build_request(profiles_for("orders_clean.csv"), orders)
    blob = json.dumps(request)
    assert "row_count" not in blob and "null_count" not in blob


# ---------------------------------------------------------------- embeddings


def test_embeddings_degrade_when_the_model_is_absent(orders, monkeypatch):
    monkeypatch.setattr(embeddings, "_model", _raise_unavailable)
    assert embeddings.propose(profiles_for("orders_clean.csv"), orders) == {}
    assert embeddings.available() is False


def _raise_unavailable():
    raise embeddings.Unavailable("not cached")


def test_ensemble_survives_every_optional_provider_missing(orders, monkeypatch):
    monkeypatch.setattr(embeddings, "_model", _raise_unavailable)
    spec = ensemble.propose(profiles_for("orders_clean.csv"), orders, source_id="s1")
    assert spec.mapped_count == 12
    status = {s["provider"]: s for s in ensemble.provider_status()}
    assert status["embedding"]["available"] is False
    assert "fetch-model" in status["embedding"]["detail"]


@pytest.mark.skipif(not embeddings.available(), reason="embedding model not cached locally")
def test_embedding_provider_ranks_sensibly(orders):
    result = embeddings.propose(profiles_for("orders_clean.csv"), orders)
    assert result["order_id"][0].target_field == "order_id"
    assert result["order_id"][0].provenance[0].provider == "embedding"


# ---------------------------------------------------------------- spec model + store


def _spec(source_id="s1", **over) -> MappingSpec:
    base = dict(
        source_id=source_id,
        target_schema="orders",
        target_version=1,
        mappings=[
            FieldMapping(target_field="order_id", source_column="Ord #"),
            FieldMapping(target_field="status", source_column="X7"),
        ],
    )
    return MappingSpec(**{**base, **over})


def test_content_hash_ignores_confidence_and_provenance():
    """Re-running the proposer must not invent a version when decisions are unchanged."""
    plain = _spec()
    annotated = _spec(
        mappings=[
            FieldMapping(
                target_field="order_id",
                source_column="Ord #",
                confidence=0.9,
                provenance=[Evidence(provider="llm", score=0.9)],
            ),
            FieldMapping(target_field="status", source_column="X7", confidence=0.5),
        ]
    )
    assert plain.content_hash() == annotated.content_hash()


def test_content_hash_changes_with_a_real_decision():
    changed = _spec(
        mappings=[
            FieldMapping(target_field="order_id", source_column="Ord #"),
            FieldMapping(target_field="status", source_column="Ln"),
        ]
    )
    assert changed.content_hash() != _spec().content_hash()


def test_content_hash_changes_with_transforms():
    changed = _spec(
        mappings=[
            FieldMapping(
                target_field="order_id", source_column="Ord #", transforms=[TransformOp(op="strip")]
            ),
            FieldMapping(target_field="status", source_column="X7"),
        ]
    )
    assert changed.content_hash() != _spec().content_hash()


def test_content_hash_is_order_independent():
    reordered = _spec(
        mappings=[
            FieldMapping(target_field="status", source_column="X7"),
            FieldMapping(target_field="order_id", source_column="Ord #"),
        ]
    )
    assert reordered.content_hash() == _spec().content_hash()


def test_mapping_rejects_both_source_and_literal():
    with pytest.raises(ValueError, match="not both"):
        FieldMapping(target_field="currency", source_column="Curr", literal="USD")


def test_spec_rejects_duplicate_target_fields():
    with pytest.raises(ValueError, match="duplicate target fields"):
        MappingSpec(
            source_id="s1",
            target_schema="orders",
            target_version=1,
            mappings=[
                FieldMapping(target_field="sku", source_column="a"),
                FieldMapping(target_field="sku", source_column="b"),
            ],
        )


def test_saving_an_unchanged_spec_is_a_noop(client):
    first = store.save(_spec())
    second = store.save(_spec())
    assert first["id"] == second["id"]
    assert second["version"] == 1
    assert len(store.history("s1")) == 1


def test_saving_a_changed_spec_creates_a_linked_version(client):
    first = store.save(_spec())
    changed = _spec(
        mappings=[
            FieldMapping(target_field="order_id", source_column="Ord #"),
            FieldMapping(target_field="status", source_column="Ln"),
        ]
    )
    second = store.save(changed, created_by="human")

    assert second["version"] == 2
    assert second["parent_id"] == first["id"]
    assert second["created_by"] == "human"
    assert [r["version"] for r in store.history("s1")] == [2, 1]
    assert store.head("s1")["id"] == second["id"]


def test_diff_reports_changed_fields():
    older = _spec()
    newer = _spec(
        mappings=[
            FieldMapping(target_field="order_id", source_column="Ord #"),
            FieldMapping(target_field="status", source_column="Ln"),
        ]
    )
    changes = store.diff(older, newer)
    assert changes == [
        {"target_field": "status", "before": "X7", "after": "Ln", "transforms_changed": False}
    ]


# ---------------------------------------------------------------- API


def _ingest(client, name="orders_messy.csv") -> str:
    response = client.post(f"/api/sources/from-sample?name={name}")
    assert response.status_code == 200, response.text
    return response.json()["source_id"]


def test_propose_endpoint(client):
    source_id = _ingest(client)
    response = client.post(f"/api/sources/{source_id}/propose?use_llm=false&use_embeddings=false")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["mapped"] == 12  # every field except customer_name
    assert body["total"] == 13
    assert body["version"] == 1
    assert body["spec"]["mappings"][0]["target_field"] == "order_id"
    providers = {p["provider"]: p for p in body["providers"]}
    assert providers["heuristic"]["available"] is True


def test_propose_is_idempotent(client):
    source_id = _ingest(client)
    first = client.post(f"/api/sources/{source_id}/propose?use_llm=false&use_embeddings=false")
    second = client.post(f"/api/sources/{source_id}/propose?use_llm=false&use_embeddings=false")
    assert first.json()["spec_id"] == second.json()["spec_id"]
    assert second.json()["version"] == 1


def test_propose_unknown_source_404(client):
    assert client.post("/api/sources/nope/propose").status_code == 404


def test_propose_unknown_schema_404(client):
    source_id = _ingest(client)
    assert client.post(f"/api/sources/{source_id}/propose?schema=invoices").status_code == 404


def test_get_spec_before_proposing_404(client):
    source_id = _ingest(client)
    assert client.get(f"/api/sources/{source_id}/spec").status_code == 404


def test_put_spec_records_a_human_correction(client):
    source_id = _ingest(client)
    client.post(f"/api/sources/{source_id}/propose?use_llm=false&use_embeddings=false")

    current = client.get(f"/api/sources/{source_id}/spec").json()
    spec = current["spec"]
    for mapping in spec["mappings"]:
        if mapping["target_field"] == "customer_name":
            mapping["source_column"] = "Cust"
            mapping["decided_by"] = "human"

    response = client.put(f"/api/sources/{source_id}/spec", json=spec)
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert response.json()["created_by"] == "human"

    history = client.get(f"/api/sources/{source_id}/spec/history").json()
    assert [h["version"] for h in history] == [2, 1]


def test_put_spec_rejects_mismatched_source(client):
    source_id = _ingest(client)
    client.post(f"/api/sources/{source_id}/propose?use_llm=false&use_embeddings=false")
    spec = client.get(f"/api/sources/{source_id}/spec").json()["spec"]
    spec["source_id"] = "somebody-else"
    response = client.put(f"/api/sources/{source_id}/spec", json=spec)
    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_put_spec_rejects_unknown_target_field(client):
    source_id = _ingest(client)
    client.post(f"/api/sources/{source_id}/propose?use_llm=false&use_embeddings=false")
    spec = client.get(f"/api/sources/{source_id}/spec").json()["spec"]
    spec["mappings"][0]["target_field"] = "not_a_field"
    response = client.put(f"/api/sources/{source_id}/spec", json=spec)
    assert response.status_code == 400
    assert "unknown target fields" in response.json()["detail"]


def test_replay_without_recordings_reports_itself_unavailable(tmp_path, monkeypatch):
    """Reporting `replay` as available with an empty fixture dir would credit the ensemble
    with a voice that contributed nothing."""
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    monkeypatch.setenv("LLM_FIXTURES_DIR", str(tmp_path))
    get_settings.cache_clear()

    status = {s["provider"]: s for s in ensemble.provider_status()}
    assert status["llm"]["available"] is False
    assert "record-llm" in status["llm"]["detail"]

    (tmp_path / "deadbeef.json").write_text("{}", encoding="utf-8")
    status = {s["provider"]: s for s in ensemble.provider_status()}
    assert status["llm"]["available"] is True
    assert "1 recorded" in status["llm"]["detail"]
