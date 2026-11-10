"""Rules/household JSON Schemas: the pipeline's keys all appear in the rules schema, the validator names bad keys, and save enforces it."""
from __future__ import annotations
import ast, html, json, re

import pytest

from conftest import GOLDEN, ROOT, SAMPLE
from app import schema

PIPELINE = ROOT / "pipeline"


def pipeline_rule_keys() -> set[str]:
    """Keys build_ledger.py's RULES defaults declare, plus every key any pipeline script reads from RULES / RULESA / rules.json."""
    tree = ast.parse((PIPELINE / "build_ledger.py").read_text())
    defaults = next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "RULES" for t in n.targets))
    keys = {k.value for k in defaults.keys}
    for f in PIPELINE.glob("*.py"):
        keys |= set(re.findall(r'\bRULES[A]?(?:\.get\(|\[)"(\w+)"', f.read_text()))
    return keys


def test_every_key_the_pipeline_reads_is_in_the_schema_and_nothing_else():
    props = set(schema.load("rules")["properties"])
    assert pipeline_rule_keys() <= props, f"pipeline reads keys missing from the schema: {pipeline_rule_keys() - props}"
    assert props <= pipeline_rule_keys(), f"schema lists keys the pipeline never reads: {props - pipeline_rule_keys()}"


def test_pipeline_defaults_and_shipped_rules_validate():
    tree = ast.parse((PIPELINE / "build_ledger.py").read_text())
    defaults = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "RULES" for t in n.targets)))
    assert schema.validate(defaults, schema.load("rules")) == []
    for p in (GOLDEN / "rules.json", SAMPLE / "rules.json"):
        assert schema.validate(json.loads(p.read_text()), schema.load("rules")) == [], p
    for p in (SAMPLE / "household.json", PIPELINE / "household.json"):
        assert schema.validate(json.loads(p.read_text()), schema.load("household")) == [], p


def test_unknown_key_is_named_with_a_suggestion():
    errs = schema.validate({"employer": [{"match": "x", "name": "Y"}]}, schema.load("rules"))
    assert errs == ['employer: unknown key (did you mean "employers"?); the pipeline would ignore it']


def test_nested_errors_name_the_key_and_regexes_are_compiled():
    rules = {"employers": [{"match": "ok", "name": "A"}, {"match": "(unclosed", "name": "B"}, {"name": "C"}],
             "merchant_rules": [["fine", "Fine"], ["[bad", "Bad"], ["only one"]],
             "transfer_item_words": "a(", "debt_plug_until": "2025-13", "people": "Rosie"}
    errs = schema.validate(rules, schema.load("rules"))
    joined = "\n".join(errs)
    assert "employers[1].match: not a valid regular expression" in joined
    assert "employers[2].match: required" in joined
    assert "merchant_rules[1][0]: not a valid regular expression" in joined
    assert "merchant_rules[2]: must have 2 item(s), has 1" in joined
    assert "transfer_item_words: not a valid regular expression" in joined
    assert "debt_plug_until:" in joined and "people: must be array, not a string" in joined
    assert not any(e.startswith(("employers[0]", "merchant_rules[0]")) for e in errs)


def test_household_schema():
    s = schema.load("household")
    assert schema.validate({"name": "Bag End", "title": "Bag End Ledger", "currency": "USD", "theme": "verdigris"}, s) == []
    errs = schema.validate({"name": "", "currency": "usd", "colour": 1}, s)
    assert any(e.startswith("name: must not be empty") for e in errs) and "title: required" in errs
    assert any(e.startswith("currency:") for e in errs) and any(e.startswith("colour: unknown key") for e in errs)
    assert schema.validate([], s) == ["must be object, not a list"]


def test_null_and_bool_types():
    s = schema.load("rules")
    assert schema.validate({"cpi_base_month": None, "employer_before": None, "transfer_item_words": None}, s) == []
    assert schema.validate({"people": [True]}, s) == ["people[0]: must be string, not true/false"]


# ---- the app: save validates, help text renders, schema is published ----
def saved(client, key, body):
    r = client.post(f"/settings/{key}", data={"body": body})
    assert r.status_code == 303
    return client.get("/settings").text


def test_save_rejects_unknown_key_and_keeps_stored_rules(client, reset_db):
    from app import db
    page = saved(client, "rules", json.dumps({"employer": []}))
    assert "Not saved" in page and "employer: unknown key" in page and "did you mean" in page
    with db.pool.connection() as conn:
        assert "employer" not in (db.get_setting(conn, "rules", {}) or {})


def test_save_rejects_bad_regex_and_accepts_valid_rules(client, reset_db):
    from app import db
    page = saved(client, "rules", json.dumps({"employers": [{"match": "(", "name": "X"}]}))
    assert "employers[0].match: not a valid regular expression" in page
    good = {"cpi_base_month": "2025-04", "employers": [{"match": "hobbiton", "name": "Hobbiton Mill"}], "merchant_rules": [["bywater", "Bywater Market"]]}
    assert "Saved rules" in saved(client, "rules", json.dumps(good))
    with db.pool.connection() as conn:
        assert db.get_setting(conn, "rules", {}) == good
    page = saved(client, "household", json.dumps({"name": "Bag End"}))
    assert "Not saved" in page and "title: required" in page


def test_household_save_and_help_text_and_published_schema(client, reset_db):
    assert "Saved household" in saved(client, "household", json.dumps({"name": "Bag End", "title": "Bag End Ledger", "currency": "USD"}))
    page = client.get("/settings").text
    s = schema.load("rules")
    assert "Keys and what they do" in page
    assert "<code>employers</code>" in page and html.escape(s["properties"]["employers"]["description"]) in page
    assert "/schemas/rules.schema.json" in page
    r = client.get("/schemas/rules.schema.json")
    assert r.status_code == 200 and r.json() == s
    assert client.get("/schemas/household.schema.json").json() == schema.load("household")
    assert client.get("/schemas/nope.schema.json").status_code == 404
