import json
import os
from pathlib import Path

import pytest

from app.ingest.readers import land
from app.profile import patterns, semantics
from app.profile.profiler import profile_frame

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
GOLDEN = Path(__file__).parent / "golden"
FIXTURES = ["orders_clean.csv", "orders_messy.csv", "orders_euro.xlsx", "orders_nested.jsonl"]


def _profile(name: str) -> list[dict]:
    return profile_frame(land(SAMPLES / name).frame)


def _by_name(profiles: list[dict], column: str) -> dict:
    return next(p for p in profiles if p["name"] == column)


@pytest.mark.parametrize("name", FIXTURES)
def test_profile_matches_golden(name):
    """Set UPDATE_GOLDEN=1 to rewrite the snapshots after an intentional change."""
    actual = _profile(name)
    path = GOLDEN / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert path.is_file(), f"missing golden for {name}; run with UPDATE_GOLDEN=1"
    assert actual == json.loads(path.read_text())


def test_pattern_mining_produces_usable_regex_masks():
    profiles = _profile("orders_messy.csv")
    assert _by_name(profiles, "Ord #")["patterns"][0]["pattern"] == r"[A-Z]{3}-\d{6}"
    assert _by_name(profiles, "Item SKU")["patterns"][0]["pattern"] == r"[A-Z]{3}-\d{4}"


def test_mask_shapes():
    assert patterns.mask("ORD-000123") == r"[A-Z]{3}-\d{6}"
    assert patterns.mask("12/31/2024") == r"\d{2}\/\d{2}\/\d{4}"
    assert patterns.mask("a") == "[a-z]"
    assert patterns.mask("") == ""


def test_meaningless_column_name_is_identified_by_its_values():
    """X7 carries no naming signal; its low distinct count under case folding is what
    identifies it as a status enum."""
    x7 = _by_name(_profile("orders_messy.csv"), "X7")
    assert "enum" in x7["semantics"]
    assert "inconsistent_case" in x7["semantics"]
    assert x7["normalized_distinct_count"] < x7["distinct_count"]
    assert x7["normalized_distinct_count"] == 5


def test_date_order_is_discriminated_from_the_values():
    """The same \\d{2}/\\d{2}/\\d{4} shape means different things in the US and German
    exports; only day-of-month values above 12 can tell them apart."""
    us = _by_name(_profile("orders_messy.csv"), "Ordered On")
    de = _by_name(_profile("orders_euro.xlsx"), "Termine_Bestelldatum")
    assert us["date_candidates"][0]["label"] == "MM/DD/YYYY"
    assert de["date_candidates"][0]["label"] == "DD/MM/YYYY"
    assert us["date_ambiguous"] is False
    assert de["date_ambiguous"] is False


def test_undecidable_date_order_is_flagged_rather_than_guessed():
    candidates = semantics.date_candidates(["01/02/2024", "03/04/2024", "05/06/2024"])
    labels = {c["label"] for c in candidates}
    assert {"MM/DD/YYYY", "DD/MM/YYYY"} <= labels
    assert candidates[0]["parsed"] == candidates[1]["parsed"]


def test_european_decimals_are_detected_and_parsed():
    price = _by_name(_profile("orders_euro.xlsx"), "Betrag_Einzelpreis")
    assert price["inferred_type"] == "european_decimal"
    assert price["numeric"]["max"] == 1142.0


def test_currency_and_percentage_semantics():
    profiles = _profile("orders_messy.csv")
    assert "currency" in _by_name(profiles, "Price Each")["semantics"]
    assert "percentage" in _by_name(profiles, "Disc %")["semantics"]


def test_null_tokens_count_as_null_not_as_values():
    """N/A, -, NULL and blanks are all nulls; counting them as values would inflate
    cardinality and corrupt every downstream signal."""
    shipped = _by_name(_profile("orders_messy.csv"), "Shipped")
    assert shipped["null_rate"] > 0
    values = {v["value"] for v in shipped["top_values"]}
    assert not values & {"N/A", "-", "NULL", "n/a", ""}


def test_null_token_recognition():
    for token in ["", "-", "N/A", "n/a", "NULL", "none", "  "]:
        assert semantics.is_null_token(token)
    assert not semantics.is_null_token("0")
    assert not semantics.is_null_token("pending")


def test_integer_wins_over_decimal_for_whole_numbers():
    quantity = _by_name(_profile("orders_clean.csv"), "quantity")
    assert quantity["inferred_type"] == "integer"


def test_clean_fixture_has_no_nulls_except_open_shipments():
    profiles = _profile("orders_clean.csv")
    nulled = {p["name"] for p in profiles if p["null_rate"] > 0}
    assert nulled == {"ship_date"}
