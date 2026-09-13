"""Phase 6: validating transformed rows and explaining every rejection."""

import csv
import json
from pathlib import Path

import polars as pl
import pytest

from app.ingest import landing
from app.ingest.readers import SRC_ROW
from app.models.mapping import FieldMapping
from app.propose import ensemble
from app.target import loader
from app.transform import polars_engine, runner
from app.validate import report as validation
from app.validate.report import StructuralError

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
JARGON = ["isin", "not_nullable", "str_matches", "greater_than", "less_than", "str_length"]


@pytest.fixture
def orders():
    return loader.get("orders")


def _prepare(name: str, orders):
    source_id = landing.register(SAMPLES / name)["source_id"]
    spec = ensemble.propose(
        landing.get_profiles(source_id),
        orders,
        source_id=source_id,
        use_llm=False,
        use_embeddings=False,
    )
    return spec, landing.load_frame(source_id)


def _check(spec, landed, orders) -> validation.Report:
    return validation.check(polars_engine.apply(spec, landed, orders), landed, spec, orders)


def _edit(landed: pl.DataFrame, line: int, **values: str) -> pl.DataFrame:
    return landed.with_columns(
        pl.when(pl.col(SRC_ROW) == line).then(pl.lit(value)).otherwise(pl.col(column)).alias(column)
        for column, value in values.items()
    )


# ---------------------------------------------------------------- fixtures


def test_clean_file_passes(orders):
    report = _check(*_prepare("orders_clean.csv", orders), orders)
    assert report.problems == []
    assert report.rows_valid == report.rows_in == 30
    assert report.summary() == "All 30 rows passed."


def test_messy_rejections_point_at_the_original_line_and_value(orders):
    report = _check(*_prepare("orders_messy.csv", orders), orders)
    found = {(p.line, p.field): p for p in report.problems}

    assert set(found) == {
        (13, "order_date"),
        (17, "quantity"),
        (20, "customer_id"),
        (24, "unit_price"),
        (28, "discount_pct"),
        (31, "order_date"),
    }
    assert (report.rows_valid, report.rows_rejected) == (24, 6)

    date = found[(13, "order_date")]
    assert (date.kind, date.value) == ("unparseable", "13/45/2024")
    assert date.message == (
        'Line 13: "Ordered On" is "13/45/2024", which is not a valid date in the format MM/DD/YYYY.'
    )

    quantity = found[(17, "quantity")]
    assert (quantity.kind, quantity.value) == ("missing", "NULL")
    assert quantity.message == 'Line 17: "Qty" is empty, but quantity is required.'


def test_identical_failures_are_grouped(orders):
    report = _check(*_prepare("orders_euro.xlsx", orders), orders)
    groups = report.groups()

    assert report.rows_rejected == 30
    assert {p.kind for p in report.problems} == {"not_allowed"}
    assert {g["value"] for g in groups} == {
        "retourniert",
        "offen",
        "storniert",
        "geliefert",
        "versandt",
    }
    assert sum(g["count"] for g in groups) == 30
    assert groups[0]["count"] == max(g["count"] for g in groups)
    assert '"Termine_Status"' in groups[0]["message"]
    assert groups[0]["message"].endswith(
        "Use one of: pending, shipped, delivered, cancelled, returned."
    )


def test_nested_key_drift_is_reported_as_missing(orders):
    """`qty` rows leave `quantity` empty; that is a missing value, not an unreadable one."""
    report = _check(*_prepare("orders_nested.jsonl", orders), orders)
    assert {p.field for p in report.problems} == {"quantity", "discount_pct"}
    assert {p.kind for p in report.problems} == {"missing"}


@pytest.mark.parametrize("sample", ["orders_messy.csv", "orders_euro.xlsx", "orders_nested.jsonl"])
def test_messages_never_leak_validator_jargon(sample, orders):
    report = _check(*_prepare(sample, orders), orders)
    assert report.problems
    for problem in report.problems:
        assert not any(term in problem.message for term in JARGON), problem.message


# ---------------------------------------------------------------- each kind of problem


def test_each_constraint_has_a_plain_english_message(orders):
    spec, landed = _prepare("orders_clean.csv", orders)
    lines = landed[SRC_ROW].to_list()
    landed = _edit(landed, lines[0], quantity="0", discount_pct="150")
    landed = _edit(landed, lines[1], order_id="12345", currency="XYZ")
    landed = _edit(landed, lines[2], quantity="four", product_name="x" * 250)

    report = _check(spec, landed, orders)
    got = {(p.line, p.field): p.message for p in report.problems}

    assert report.rows_rejected == 3
    assert got[(lines[0], "quantity")] == (
        f'Line {lines[0]}: "quantity" is "0", but quantity must be at least 1.'
    )
    assert got[(lines[0], "discount_pct")] == (
        f'Line {lines[0]}: "discount_pct" is "150", but discount pct must be at most 100.'
    )
    assert got[(lines[1], "order_id")] == (
        f'Line {lines[1]}: "order_id" is "12345", which does not look like a valid order id'
        " (expected something like ORD-000101)."
    )
    assert got[(lines[1], "currency")] == (
        f'Line {lines[1]}: "currency" is "XYZ", which is not an accepted currency.'
        " Use one of: USD, EUR, GBP, JPY, CAD."
    )
    assert got[(lines[2], "quantity")] == (
        f'Line {lines[2]}: "quantity" is "four", which is not a whole number.'
    )
    assert got[(lines[2], "product_name")] == (
        f'Line {lines[2]}: "product_name" is 250 characters long,'
        " but product name allows at most 200."
    )


def test_duplicate_keys_reject_every_copy_and_name_the_other_line(orders):
    spec, landed = _prepare("orders_clean.csv", orders)
    lines = landed[SRC_ROW].to_list()
    first = landed.filter(pl.col(SRC_ROW) == lines[0]).row(0, named=True)
    landed = _edit(landed, lines[5], order_id=first["order_id"], line_number=first["line_number"])

    dupes = [p for p in _check(spec, landed, orders).problems if p.kind == "duplicate_key"]

    assert {p.line for p in dupes} == {lines[0], lines[5]}
    message = next(p for p in dupes if p.line == lines[0]).message
    assert message == (
        f"Line {lines[0]}: order id {first['order_id']} with line number"
        f" {first['line_number']} also appears on line {lines[5]}."
    )


def test_unmapped_required_field_rejects_every_row_as_one_group(orders):
    spec, landed = _prepare("orders_clean.csv", orders)
    mappings = [
        FieldMapping(target_field="currency") if m.target_field == "currency" else m
        for m in spec.mappings
    ]
    report = _check(spec.model_copy(update={"mappings": mappings}), landed, orders)

    assert report.rows_rejected == 30
    [group] = report.groups()
    assert (group["kind"], group["count"]) == ("unmapped", 30)
    assert group["message"] == "No column in the file provides currency, which is required."


# ---------------------------------------------------------------- structural failures


def test_wrong_dtype_is_structural_not_a_row_rejection(orders):
    spec, landed = _prepare("orders_clean.csv", orders)
    frame = polars_engine.apply(spec, landed, orders).with_columns(pl.col("quantity").cast(pl.Utf8))
    with pytest.raises(StructuralError, match="quantity"):
        validation.check(frame, landed, spec, orders)


def test_untraceable_output_is_structural(orders):
    spec, landed = _prepare("orders_clean.csv", orders)
    frame = polars_engine.apply(spec, landed, orders).drop(SRC_ROW)
    with pytest.raises(StructuralError, match=SRC_ROW):
        validation.check(frame, landed, spec, orders)


# ---------------------------------------------------------------- artifacts and runs


def test_rejections_csv_has_one_line_per_problem(orders, tmp_path):
    report = _check(*_prepare("orders_messy.csv", orders), orders)
    paths = validation.write(report, tmp_path, "run_x", run_id="x")

    assert paths["rejections"].read_bytes().startswith(b"\xef\xbb\xbf")
    with paths["rejections"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["line", "column", "value", "problem"]
    assert len(rows) - 1 == len(report.problems)
    assert [
        "13",
        "Ordered On",
        "13/45/2024",
        '"Ordered On" is "13/45/2024", which is not a valid date in the format MM/DD/YYYY.',
    ] in rows

    body = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert (body["run_id"], body["rows_rejected"]) == ("x", 6)


def test_run_keeps_only_valid_rows_and_records_rejections(orders):
    spec, _ = _prepare("orders_messy.csv", orders)
    result = runner.run(spec, orders, engine="duckdb")

    assert (result.rows_out, result.rows_valid, result.rows_rejected) == (30, 24, 6)
    assert pl.read_parquet(result.output_path).height == 24
    assert result.report_path.is_file() and result.rejections_path.is_file()

    [run] = runner.list_runs(spec.source_id)
    assert (run["rows_rejected"], run["rows_valid"]) == (6, 24)
    assert runner.get_run(result.run_id)["report_path"] == str(result.report_path)


def test_both_engines_reject_the_same_rows_for_the_same_reasons(orders):
    spec, _ = _prepare("orders_messy.csv", orders)
    a = runner.run(spec, orders, engine="duckdb", persist=False).report
    b = runner.run(spec, orders, engine="polars", persist=False).report
    assert [p.to_dict() for p in a.problems] == [p.to_dict() for p in b.problems]


def test_run_without_validation_keeps_every_row(orders):
    spec, _ = _prepare("orders_messy.csv", orders)
    result = runner.run(spec, orders, validate=False, persist=False)
    assert result.report is None and result.rows_rejected is None
    assert pl.read_parquet(result.output_path).height == 30


# ---------------------------------------------------------------- API


def _prepared_source(client, name: str = "orders_messy.csv") -> str:
    source_id = client.post(f"/api/sources/from-sample?name={name}").json()["source_id"]
    client.post(f"/api/sources/{source_id}/propose?use_llm=false&use_embeddings=false")
    return source_id


def test_transform_response_carries_the_rejection_summary(client):
    source_id = _prepared_source(client)
    body = client.post(f"/api/sources/{source_id}/transform").json()

    assert (body["rows_valid"], body["rows_rejected"]) == (24, 6)
    assert body["summary"] == "24 of 30 rows passed. 6 rows were rejected for 6 problems."
    assert body["groups"]


def test_report_and_rejections_csv_endpoints(client):
    source_id = _prepared_source(client)
    run_id = client.post(f"/api/sources/{source_id}/transform").json()["run_id"]

    report = client.get(f"/api/sources/{source_id}/transform/runs/{run_id}/report")
    assert report.status_code == 200
    assert report.json()["rows_rejected"] == 6

    download = client.get(f"/api/sources/{source_id}/transform/runs/{run_id}/rejections.csv")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")
    assert "Ordered On" in download.text


def test_unknown_run_is_404(client):
    source_id = _prepared_source(client)
    assert client.get(f"/api/sources/{source_id}/transform/runs/nope/report").status_code == 404
