"""Phase 5: compiling a MappingSpec to SQL and Polars.

The determinism tests are the point of this phase. Two independent compilers and a rendered
artifact must agree exactly on every fixture; anything less means the SQL a customer runs in
production is not the transform that was reviewed.
"""

from pathlib import Path

import duckdb
import polars as pl
import pytest

from app.ingest import landing
from app.models.mapping import FieldMapping, MappingSpec, TransformOp
from app.propose import ensemble
from app.target import loader
from app.transform import pipeline, polars_engine, runner, sql
from app.transform.pipeline import CompileError

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
FIXTURES = ["orders_clean.csv", "orders_messy.csv", "orders_euro.xlsx", "orders_nested.jsonl"]


@pytest.fixture
def orders():
    return loader.get("orders")


@pytest.fixture
def registered(isolated_catalog):
    def _register(name: str) -> tuple[str, MappingSpec]:
        source_id = landing.register(SAMPLES / name)["source_id"]
        spec = ensemble.propose(
            landing.get_profiles(source_id),
            loader.get("orders"),
            source_id=source_id,
            use_llm=False,
            use_embeddings=False,
        )
        return source_id, spec

    return _register


def _render_and_exec(spec: MappingSpec, schema) -> callable:
    namespace: dict = {}
    exec(compile(polars_engine.render(spec, schema), "generated.py", "exec"), namespace)
    return namespace["transform"]


# ---------------------------------------------------------------- determinism


@pytest.mark.parametrize("sample", FIXTURES)
def test_both_engines_and_the_artifact_agree(sample, registered, orders):
    source_id, spec = registered(sample)
    frame = landing.load_frame(source_id)

    from_sql = runner._run_duckdb(spec, orders, str(landing.landed_path(source_id)))
    from_polars = polars_engine.apply(spec, frame, orders)
    from_artifact = _render_and_exec(spec, orders)(frame)

    assert from_sql.equals(from_polars), f"{sample}: SQL and Polars disagree"
    assert from_polars.equals(from_artifact), f"{sample}: rendered artifact disagrees"
    assert from_sql.schema == from_polars.schema


@pytest.mark.parametrize("sample", FIXTURES)
def test_output_matches_the_target_schema(sample, registered, orders):
    """The compiled output must satisfy the phase 3 Pandera schema on dtypes and columns."""
    from app.target import pandera_schema as ps

    source_id, spec = registered(sample)
    frame = polars_engine.apply(spec, landing.load_frame(source_id), orders).drop("_src_row")

    assert frame.columns == orders.field_names
    expected = ps.empty_frame(orders)
    assert frame.schema == expected.schema


@pytest.mark.parametrize("sample", FIXTURES)
def test_no_required_column_is_entirely_null(sample, registered, orders):
    """Agreeing engines can agree on nothing: nested dates once parsed to null in both."""
    source_id, spec = registered(sample)
    frame = polars_engine.apply(spec, landing.load_frame(source_id), orders)
    empty = [
        f.name
        for f in orders.fields
        if not f.nullable and frame[f.name].null_count() == frame.height
    ]
    assert not empty, f"{sample}: required columns with no values at all: {empty}"


# ---------------------------------------------------------------- op semantics


def _apply_ops(values: list[str | None], ops: list[TransformOp], dtype: str) -> list:
    """Run one pipeline through both engines and assert they agree, returning the result."""
    frame = pl.DataFrame({"c": values}, schema={"c": pl.Utf8})

    expr = pl.col("c")
    for op in ops:
        expr = polars_engine._apply(expr, op, dtype)
    polars_out = frame.select(expr.alias("c"))["c"].to_list()

    expression = sql.ident("c")
    for op in ops:
        expression = sql._apply(expression, op, dtype)
    with duckdb.connect() as conn:
        conn.register("t", frame)
        sql_out = conn.execute(f"SELECT {expression} AS c FROM t").pl()["c"].to_list()

    assert polars_out == sql_out, f"engines disagree: polars={polars_out} sql={sql_out}"
    return polars_out


CAST_STRING = TransformOp(op="cast", args={"dtype": "string"})


def test_strip_and_case():
    assert _apply_ops(["  A b  "], [TransformOp(op="strip"), CAST_STRING], "string") == ["A b"]
    assert _apply_ops(["Ab"], [TransformOp(op="lower"), CAST_STRING], "string") == ["ab"]
    assert _apply_ops(["Ab"], [TransformOp(op="upper"), CAST_STRING], "string") == ["AB"]


def test_null_if_is_case_insensitive_and_trimmed():
    ops = [
        TransformOp(op="null_if", args={"tokens": ["", "-", "N/A", "null"]}),
        TransformOp(op="cast", args={"dtype": "string"}),
    ]
    assert _apply_ops([" n/a ", "-", "NULL", "", "real"], ops, "string") == [
        None,
        None,
        None,
        None,
        "real",
    ]


def test_strip_currency_handles_symbols_and_codes():
    ops = [TransformOp(op="strip_currency"), TransformOp(op="cast", args={"dtype": "string"})]
    assert _apply_ops(["$1,142.00", "€0,85", "38.50 USD"], ops, "string") == [
        "1,142.00",
        "0,85",
        "38.50",
    ]


def test_decimal_locales():
    us = [
        TransformOp(op="parse_decimal", args={"locale": "us"}),
        TransformOp(op="cast", args={"dtype": "decimal"}),
    ]
    eu = [
        TransformOp(op="parse_decimal", args={"locale": "eu"}),
        TransformOp(op="cast", args={"dtype": "decimal"}),
    ]
    assert _apply_ops(["1,142.00"], us, "decimal") == [1142.0]
    assert _apply_ops(["1.234,56", "0,85"], eu, "decimal") == [1234.56, 0.85]


def test_parse_percent():
    ops = [
        TransformOp(op="parse_percent"),
        TransformOp(op="cast", args={"dtype": "decimal"}),
    ]
    assert _apply_ops(["10%", "0 %", "5"], ops, "decimal") == [10.0, 0.0, 5.0]


def test_parse_date_disambiguates_by_format():
    def parse(value: str, fmt: str):
        ops = [
            TransformOp(op="parse_date", args={"format": fmt}),
            TransformOp(op="cast", args={"dtype": "date"}),
        ]
        return _apply_ops([value], ops, "date")[0]

    import datetime as dt

    assert parse("03/08/2024", "%m/%d/%Y") == dt.date(2024, 3, 8)
    assert parse("03/08/2024", "%d/%m/%Y") == dt.date(2024, 8, 3)


def test_map_values_keeps_unmapped_by_default():
    ops = [
        TransformOp(op="map_values", args={"mapping": {"RETURNED": "returned"}}),
        TransformOp(op="cast", args={"dtype": "string"}),
    ]
    assert _apply_ops(["RETURNED", "pending"], ops, "string") == ["returned", "pending"]


def test_map_values_default_replaces_unmapped():
    ops = [
        TransformOp(
            op="map_values",
            args={"mapping": {"RETURNED": "returned"}, "default": "unknown"},
        ),
        TransformOp(op="cast", args={"dtype": "string"}),
    ]
    assert _apply_ops(["RETURNED", "wat"], ops, "string") == ["returned", "unknown"]


def test_regex_extract():
    ops = [
        TransformOp(op="regex_extract", args={"pattern": r"SKU-(\d+)"}),
        TransformOp(op="cast", args={"dtype": "integer"}),
    ]
    assert _apply_ops(["SKU-1120"], ops, "integer") == [1120]


def test_default_fills_nulls():
    ops = [
        TransformOp(op="null_if", args={"tokens": [""]}),
        TransformOp(op="default", args={"value": "USD"}),
        TransformOp(op="cast", args={"dtype": "string"}),
    ]
    assert _apply_ops(["", "EUR"], ops, "string") == ["USD", "EUR"]


def test_unparseable_values_become_null_rather_than_raising():
    """Failure is deferred to phase 6, which explains it against the original value."""
    ops = [TransformOp(op="cast", args={"dtype": "integer"})]
    assert _apply_ops(["12", "not a number", None], ops, "integer") == [12, None, None]

    dates = [
        TransformOp(op="parse_date", args={"format": "%m/%d/%Y"}),
        TransformOp(op="cast", args={"dtype": "date"}),
    ]
    assert _apply_ops(["31/02/2024"], dates, "date") == [None]


def test_every_op_is_implemented_by_both_compilers():
    """A new op must not reach a spec before both compilers can emit it."""
    from app.models.mapping import OP_ARGS

    samples = {
        "strip": {},
        "lower": {},
        "upper": {},
        "strip_currency": {},
        "parse_percent": {},
        "null_if": {"tokens": ["x"]},
        "parse_decimal": {"locale": "us"},
        "parse_date": {"format": "%Y-%m-%d"},
        "map_values": {"mapping": {"a": "b"}},
        "regex_extract": {"pattern": "(x)"},
        "default": {"value": "v"},
        "cast": {"dtype": "string"},
    }
    assert set(samples) == set(OP_ARGS), "sample args are out of sync with the vocabulary"

    for name, args in samples.items():
        op = TransformOp(op=name, args=args)
        assert polars_engine._apply(pl.col("c"), op, "string") is not None
        assert sql._apply('"c"', op, "string")
        assert polars_engine._source('pl.col("c")', op, "string")


# ---------------------------------------------------------------- pipeline validation


def _mapping(**over) -> FieldMapping:
    base = dict(
        target_field="sku",
        source_column="Item SKU",
        transforms=[TransformOp(op="strip"), TransformOp(op="cast", args={"dtype": "string"})],
    )
    return FieldMapping(**{**base, **over})


def test_pipeline_requires_a_terminal_cast(orders):
    with pytest.raises(CompileError, match="cast must be the last step"):
        pipeline.validate(
            _mapping(
                transforms=[
                    TransformOp(op="cast", args={"dtype": "string"}),
                    TransformOp(op="strip"),
                ]
            ),
            orders,
        )


def test_pipeline_requires_exactly_one_cast(orders):
    with pytest.raises(CompileError, match="exactly one cast"):
        pipeline.validate(_mapping(transforms=[TransformOp(op="strip")]), orders)


def test_pipeline_rejects_a_cast_to_the_wrong_type(orders):
    with pytest.raises(CompileError, match="casts to integer but the target is string"):
        pipeline.validate(
            _mapping(transforms=[TransformOp(op="cast", args={"dtype": "integer"})]), orders
        )


def test_mapped_column_without_transforms_is_rejected(orders):
    with pytest.raises(CompileError, match="at least a cast"):
        pipeline.validate(_mapping(transforms=[]), orders)


def test_unmapped_field_needs_no_transforms(orders):
    pipeline.validate(FieldMapping(target_field="customer_name"), orders)


def test_spec_missing_a_target_field_is_rejected(orders):
    spec = MappingSpec(
        source_id="s1", target_schema="orders", target_version=1, mappings=[_mapping()]
    )
    with pytest.raises(CompileError, match="missing target fields"):
        pipeline.validate_spec(spec, orders)


# ---------------------------------------------------------------- artifacts


def test_sql_artifact_is_standalone(registered, orders, isolated_catalog):
    """The emitted SQL must run in a bare DuckDB with no macros or setup."""
    source_id, spec = registered("orders_messy.csv")
    script = sql.compile_script(spec, orders, str(landing.landed_path(source_id)))

    with duckdb.connect() as conn:
        frame = conn.execute(script).pl()
    assert frame.height == 30
    assert "customer_name" in frame.columns


def test_rendered_python_is_importable_and_declares_its_inputs(registered, orders):
    source_id, spec = registered("orders_euro.xlsx")
    source = polars_engine.render(spec, orders)

    namespace: dict = {}
    exec(compile(source, "generated.py", "exec"), namespace)
    assert "Betrag_Einzelpreis" in namespace["SOURCE_COLUMNS"]
    assert spec.content_hash() in source


def test_identifiers_and_literals_are_quoted():
    assert sql.ident('we"ird') == '"we""ird"'
    assert sql.quote("O'Brien") == "'O''Brien'"


def test_odd_column_names_survive_compilation(orders, isolated_catalog):
    """`Ord #` and `Disc %` are real column names from the fixtures."""
    source_id = landing.register(SAMPLES / "orders_messy.csv")["source_id"]
    spec = ensemble.propose(
        landing.get_profiles(source_id),
        orders,
        source_id=source_id,
        use_llm=False,
        use_embeddings=False,
    )
    statement = sql.compile_select(spec, orders, str(landing.landed_path(source_id)))
    assert '"Ord #"' in statement and '"Disc %"' in statement
    with duckdb.connect() as conn:
        assert conn.execute(statement).pl().height == 30


def test_literal_mapping_compiles_in_both_engines(orders, isolated_catalog):
    source_id = landing.register(SAMPLES / "orders_messy.csv")["source_id"]
    spec = ensemble.propose(
        landing.get_profiles(source_id),
        orders,
        source_id=source_id,
        use_llm=False,
        use_embeddings=False,
    )
    patched = [
        FieldMapping(
            target_field="customer_name",
            literal="ACME",
            transforms=[TransformOp(op="cast", args={"dtype": "string"})],
        )
        if m.target_field == "customer_name"
        else m
        for m in spec.mappings
    ]
    spec = spec.model_copy(update={"mappings": patched})

    frame = landing.load_frame(source_id)
    from_polars = polars_engine.apply(spec, frame, orders)
    from_sql = runner._run_duckdb(spec, orders, str(landing.landed_path(source_id)))
    assert from_polars.equals(from_sql)
    assert from_polars["customer_name"].unique().to_list() == ["ACME"]


# ---------------------------------------------------------------- runner


def test_run_writes_artifacts_and_records_the_run(registered, orders):
    source_id, spec = registered("orders_clean.csv")
    from app.propose import store

    store.save(spec)

    result = runner.run(spec, orders, engine="polars")
    assert result.rows_out == 30
    assert result.sql_path.is_file() and result.python_path.is_file()
    assert result.output_path.is_file()
    assert pl.read_parquet(result.output_path).height == 30

    runs = runner.list_runs(source_id)
    assert len(runs) == 1 and runs[0]["engine"] == "polars"


def test_unknown_engine_is_rejected(registered, orders):
    _, spec = registered("orders_clean.csv")
    with pytest.raises(CompileError, match="unknown engine"):
        runner.run(spec, orders, engine="spark")


# ---------------------------------------------------------------- API


def _prepare(client, name="orders_messy.csv") -> str:
    source_id = client.post(f"/api/sources/from-sample?name={name}").json()["source_id"]
    client.post(f"/api/sources/{source_id}/propose?use_llm=false&use_embeddings=false")
    return source_id


def test_transform_endpoints(client):
    source_id = _prepare(client)

    response = client.post(f"/api/sources/{source_id}/transform?engine=polars")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows_in"] == 30 and body["rows_out"] == 30
    assert "order_id" in body["columns"]

    assert client.post(f"/api/sources/{source_id}/transform?engine=duckdb").status_code == 200
    assert len(client.get(f"/api/sources/{source_id}/transform/runs").json()) == 2


def test_compiled_sources_are_served(client):
    source_id = _prepare(client)
    statement = client.get(f"/api/sources/{source_id}/transform/sql").json()["source"]
    assert statement.strip().endswith(";")
    assert "read_parquet" in statement

    module = client.get(f"/api/sources/{source_id}/transform/python").json()["source"]
    assert "def transform(" in module
    assert "import polars as pl" in module


def test_transform_without_a_spec_is_a_conflict(client):
    source_id = client.post("/api/sources/from-sample?name=orders_clean.csv").json()["source_id"]
    response = client.post(f"/api/sources/{source_id}/transform")
    assert response.status_code == 409
    assert "propose" in response.json()["detail"]


def test_transform_unknown_source_404(client):
    assert client.post("/api/sources/nope/transform").status_code == 404


def test_invalid_engine_is_rejected_by_the_api(client):
    source_id = _prepare(client)
    assert client.post(f"/api/sources/{source_id}/transform?engine=spark").status_code == 422


def test_null_tokens_are_deduplicated():
    """The proposer suggests both `N/A` and `n/a`; the artifact should list one."""
    op = TransformOp(op="null_if", args={"tokens": ["", "-", "N/A", "n/a", "null", "NULL"]})
    assert pipeline.null_tokens(op) == ["", "-", "n/a", "null"]
    assert sql._apply('"c"', op, "string").count("'n/a'") == 1
