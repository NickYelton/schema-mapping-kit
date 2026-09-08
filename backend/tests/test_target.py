import datetime as dt

import pandera.errors as pe
import polars as pl
import pytest
import yaml

from app.models.target import Constraints, TargetField, TargetSchema
from app.target import loader
from app.target import pandera_schema as ps

# ---------------------------------------------------------------- loading


def test_orders_schema_loads():
    schema = loader.get("orders")
    assert schema.slug == "orders_v1"
    assert schema.version == 1
    assert schema.primary_key == ["order_id", "line_number"]
    assert "status" in schema.field_names
    assert len(schema.fields) == 13


def test_get_accepts_slug_and_explicit_version():
    assert loader.get("orders_v1").slug == "orders_v1"
    assert loader.get("orders", 1).slug == "orders_v1"
    assert loader.versions("orders") == [1]


def test_get_unknown_schema_raises():
    with pytest.raises(KeyError):
        loader.get("invoices")
    with pytest.raises(KeyError):
        loader.get("orders", 99)


def test_every_field_carries_matching_metadata():
    """The proposer relies on these, so an empty one is a bug rather than a style nit."""
    for field in loader.get("orders").fields:
        assert field.description.strip(), f"{field.name} has no description to embed"
        assert field.aliases, f"{field.name} has no aliases to fuzzy-match"


def _write(tmp_path, monkeypatch, filename: str, payload: dict | str):
    monkeypatch.setenv("SCHEMAS_DIR", str(tmp_path))
    from app.core.settings import get_settings

    get_settings.cache_clear()
    path = tmp_path / filename
    path.write_text(
        payload if isinstance(payload, str) else yaml.safe_dump(payload), encoding="utf-8"
    )
    return path


MINIMAL = {
    "name": "widgets",
    "version": 1,
    "fields": [{"name": "id", "dtype": "string", "nullable": False}],
}


def test_filename_must_match_declared_name_and_version(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "wrong_name.yaml", MINIMAL)
    with pytest.raises(loader.SchemaError, match="should be named widgets_v1.yaml"):
        loader.load_all()


def test_malformed_yaml_raises_schema_error(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "widgets_v1.yaml", "fields: [unclosed\n")
    with pytest.raises(loader.SchemaError, match="invalid YAML"):
        loader.load_all()


def test_non_mapping_yaml_raises_schema_error(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "widgets_v1.yaml", "- just\n- a list\n")
    with pytest.raises(loader.SchemaError, match="expected a mapping"):
        loader.load_all()


def test_unknown_key_is_rejected(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "widgets_v1.yaml", {**MINIMAL, "feilds": []})
    with pytest.raises(loader.SchemaError):
        loader.load_all()


def test_edited_schema_is_picked_up_without_restart(tmp_path, monkeypatch):
    """The cache keys on mtime, so a second read of a changed file must not be stale."""
    path = _write(tmp_path, monkeypatch, "widgets_v1.yaml", MINIMAL)
    assert loader.get("widgets").field_names == ["id"]

    payload = {**MINIMAL, "fields": [*MINIMAL["fields"], {"name": "label", "dtype": "string"}]}
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    import os

    os.utime(path, (0, 0))  # force a distinct mtime rather than racing the filesystem clock
    assert loader.get("widgets").field_names == ["id", "label"]


# ---------------------------------------------------------------- model validation


def test_constraints_reject_inverted_bounds():
    with pytest.raises(ValueError, match="min .* exceeds max"):
        Constraints(min=10, max=1)
    with pytest.raises(ValueError, match="min_length .* exceeds max_length"):
        Constraints(min_length=10, max_length=1)


def test_constraints_reject_bad_regex():
    with pytest.raises(ValueError, match="not a valid regex"):
        Constraints(pattern="[unclosed")


def test_numeric_constraint_on_string_field_is_rejected():
    with pytest.raises(ValueError, match="min/max require a numeric dtype"):
        TargetField(name="sku", dtype="string", constraints=Constraints(min=0))


def test_pattern_on_numeric_field_is_rejected():
    with pytest.raises(ValueError, match="require dtype string"):
        TargetField(name="qty", dtype="integer", constraints=Constraints(pattern="^x$"))


def test_enum_must_be_non_empty_and_unique():
    with pytest.raises(ValueError, match="at least one value"):
        TargetField(name="s", dtype="string", constraints=Constraints(enum=[]))
    with pytest.raises(ValueError, match="duplicate values"):
        TargetField(name="s", dtype="string", constraints=Constraints(enum=["a", "a"]))


def _schema(**over) -> dict:
    base = {
        "name": "t",
        "version": 1,
        "fields": [
            {"name": "a", "dtype": "string", "nullable": False},
            {"name": "b", "dtype": "integer"},
        ],
    }
    return {**base, **over}


def test_duplicate_field_names_rejected():
    payload = _schema(fields=[{"name": "a", "dtype": "string"}, {"name": "a", "dtype": "integer"}])
    with pytest.raises(ValueError, match="duplicate field names"):
        TargetSchema.model_validate(payload)


def test_primary_key_must_reference_known_fields():
    with pytest.raises(ValueError, match="unknown fields"):
        TargetSchema.model_validate(_schema(primary_key=["nope"]))


def test_primary_key_fields_must_not_be_nullable():
    with pytest.raises(ValueError, match="must not be nullable"):
        TargetSchema.model_validate(_schema(primary_key=["b"]))


def test_schema_with_no_fields_rejected():
    with pytest.raises(ValueError, match="no fields"):
        TargetSchema.model_validate(_schema(fields=[]))


def test_alias_normalization_unifies_spacing_and_case():
    field = loader.get("orders").field("unit_price")
    aliases = field.normalized_aliases()
    assert "unit_price" in aliases  # its own name
    assert "price_each" in aliases  # "Price Each" from orders_messy.csv
    assert "einzelpreis" in aliases  # the German fixture


def test_embedding_text_includes_enum_values():
    text = loader.get("orders").field("status").embedding_text()
    assert "status" in text
    assert "cancelled" in text


# ---------------------------------------------------------------- pandera compilation


def _valid_frame() -> pl.DataFrame:
    """A two-row frame matching orders_v1, with dtypes stated explicitly.

    The schema is spelled out because a column of all-nulls would otherwise infer as
    pl.Null and fail the dtype check for reasons that have nothing to do with the test.
    """
    return pl.DataFrame(
        {
            "order_id": ["ORD-000101", "BST-000102"],
            "line_number": [1, 2],
            "customer_id": ["CUST-5560", "KND-4417"],
            "customer_name": ["Kestrel Plant", None],
            "sku": ["SKU-1120", "ART-8823"],
            "product_name": ["Grommet 12mm", None],
            "quantity": [4, 24],
            "unit_price": [0.85, 38.50],
            "currency": ["USD", "EUR"],
            "discount_pct": [0.0, 10.0],
            "order_date": [dt.date(2024, 3, 8), dt.date(2024, 3, 24)],
            "ship_date": [dt.date(2024, 3, 11), None],
            "status": ["returned", "pending"],
        },
        schema={
            "order_id": pl.Utf8,
            "line_number": pl.Int64,
            "customer_id": pl.Utf8,
            "customer_name": pl.Utf8,
            "sku": pl.Utf8,
            "product_name": pl.Utf8,
            "quantity": pl.Int64,
            "unit_price": pl.Float64,
            "discount_pct": pl.Float64,
            "currency": pl.Utf8,
            "order_date": pl.Date,
            "ship_date": pl.Date,
            "status": pl.Utf8,
        },
    )


@pytest.fixture
def orders_pandera():
    return ps.build(loader.get("orders"))


def test_valid_frame_passes(orders_pandera):
    orders_pandera.validate(_valid_frame())


def test_empty_frame_matches_target_dtypes():
    schema = loader.get("orders")
    frame = ps.empty_frame(schema)
    assert frame.height == 0
    assert frame.columns == schema.field_names
    assert frame.schema["order_date"] == pl.Date
    assert frame.schema["unit_price"] == pl.Float64


@pytest.mark.parametrize(
    ("column", "bad_value", "reason"),
    [
        ("status", "SHIPPED", "upper-case status is not the canonical vocabulary"),
        ("status", "retourniert", "untranslated German status"),
        ("currency", "XYZ", "not an accepted ISO code"),
        ("unit_price", -5.0, "negative price"),
        ("discount_pct", 150.0, "discount above 100 percent"),
        ("discount_pct", -1.0, "negative discount"),
        ("quantity", 0, "quantity below the minimum of 1"),
        ("order_id", "12345", "id without the letter-prefix shape"),
        ("customer_id", "", "empty id"),
    ],
)
def test_constraint_violations_are_rejected(orders_pandera, column, bad_value, reason):
    frame = _valid_frame().with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(pl.lit(bad_value))
        .otherwise(pl.col(column))
        .alias(column)
    )
    with pytest.raises(pe.SchemaErrors) as excinfo:
        orders_pandera.validate(frame, lazy=True)
    assert column in set(excinfo.value.failure_cases["column"]), reason


def test_null_in_non_nullable_column_is_rejected(orders_pandera):
    frame = _valid_frame().with_columns(pl.Series("status", ["returned", None], dtype=pl.Utf8))
    with pytest.raises(pe.SchemaErrors):
        orders_pandera.validate(frame, lazy=True)


def test_null_in_nullable_column_is_accepted(orders_pandera):
    frame = _valid_frame().with_columns(pl.Series("ship_date", [None, None], dtype=pl.Date))
    orders_pandera.validate(frame)


def test_duplicate_primary_key_is_rejected(orders_pandera):
    """order_id repeats legitimately across lines; the pair must still be unique."""
    frame = _valid_frame().with_columns(
        pl.Series("order_id", ["ORD-000101", "ORD-000101"], dtype=pl.Utf8),
        pl.Series("line_number", [1, 1], dtype=pl.Int64),
    )
    with pytest.raises(pe.SchemaErrors):
        orders_pandera.validate(frame, lazy=True)


def test_repeated_order_id_on_distinct_lines_is_accepted(orders_pandera):
    frame = _valid_frame().with_columns(
        pl.Series("order_id", ["ORD-000101", "ORD-000101"], dtype=pl.Utf8),
        pl.Series("line_number", [1, 2], dtype=pl.Int64),
    )
    orders_pandera.validate(frame)


def test_extra_column_is_rejected_when_strict(orders_pandera):
    frame = _valid_frame().with_columns(pl.lit("x").alias("_src_row"))
    with pytest.raises(pe.SchemaErrors):
        orders_pandera.validate(frame, lazy=True)


def test_extra_column_is_allowed_when_not_strict():
    schema = ps.build(loader.get("orders"), strict=False)
    schema.validate(_valid_frame().with_columns(pl.lit("x").alias("_src_row")))


def test_missing_column_is_rejected(orders_pandera):
    with pytest.raises(pe.SchemaErrors):
        orders_pandera.validate(_valid_frame().drop("sku"), lazy=True)


def test_wrong_dtype_is_rejected_without_coercion(orders_pandera):
    """The transform is responsible for casting, so a string year must not slide through."""
    frame = _valid_frame().with_columns(pl.col("quantity").cast(pl.Utf8))
    with pytest.raises(pe.SchemaErrors):
        orders_pandera.validate(frame, lazy=True)


def test_unmapped_dtype_raises():
    with pytest.raises(ValueError, match="No Polars dtype"):
        ps.polars_dtype("complex")


# ---------------------------------------------------------------- API


def test_schemas_index(client):
    response = client.get("/api/schemas")
    assert response.status_code == 200
    body = response.json()
    assert [s["slug"] for s in body] == ["orders_v1"]
    assert body[0]["field_count"] == 13
    assert body[0]["primary_key"] == ["order_id", "line_number"]


def test_schema_detail_by_name_and_slug(client):
    for path in ("/api/schemas/orders", "/api/schemas/orders_v1", "/api/schemas/orders?version=1"):
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.json()
        assert body["name"] == "orders"
        assert body["version"] == 1
        assert len(body["fields"]) == 13


def test_schema_detail_unknown_returns_404(client):
    assert client.get("/api/schemas/invoices").status_code == 404
    assert client.get("/api/schemas/orders?version=99").status_code == 404
