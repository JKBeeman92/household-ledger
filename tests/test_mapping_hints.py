"""Mapping hints (#106) and the mapping draft (#105): one suffix rule for names that drift between years, a draft that only adds rows."""
from __future__ import annotations
import csv, io

import pytest

from conftest import GOLDEN, upload_files
from app import db, mapping_hints as mh

M = [  # (item, canonical_item, category, flow)
    ("Second Breakfast", "Second Breakfast", "Food", "expense"), ("Second Breakfast 2", "Second Breakfast", "Food", "expense"),
    ("Second Breakfast Fund", "Elevenses", "Food", "expense"),
    ("Mathom Goal", "Mathoms", "Gifts", "expense"), ("Mathom", "Mathoms", "Gifts", "expense"),
    ("Venmo Pass-Through", "Venmo Pass-Through", "Other", "exclude"), ("Venmo Pass-Through 2", "Friends' Money", "Other", "exclude"),
    ("Emergency Fund", "Emergency Fund", "Savings", "expense"),
]
MAPPING = [dict(zip(("item", "canonical_item", "category", "flow"), r)) for r in M]


@pytest.mark.parametrize("item, base", [("Second Breakfast Fund 2", "Second Breakfast"), ("Second  Breakfast   3", "Second Breakfast"),
                                        ("Party Tree GOAL", "Party Tree"), ("Fund", "Fund"), ("Goal 2", "Goal"), ("Rent", "Rent")])
def test_base_name_strips_every_year_suffix_but_never_to_nothing(item, base):
    assert mh.base_name(item) == base


def test_suffixes_are_a_rule():
    rules = {"item_name_suffixes": [r"\s+\(\d{4}\)", r"\s+savings"]}
    assert mh.base_name("Long-expected Party (2019) Savings", rules) == "Long-expected Party"
    assert mh.base_name("Second Breakfast 2", rules) == "Second Breakfast 2"   # the rule replaces the defaults


def test_suggest_prefers_a_canonical_name_then_an_item_then_a_shared_base():
    assert mh.suggest("Second Breakfast 3", MAPPING) == {"canonical_item": "Second Breakfast", "category": "Food", "flow": "expense", "like": "Second Breakfast"}
    assert mh.suggest("Mathom 2", MAPPING)["canonical_item"] == "Mathoms"            # no canonical "Mathom", but an item of that name
    assert mh.suggest("Emergency Fund 2", MAPPING)["canonical_item"] == "Emergency Fund"   # shares the base "Emergency" with a mapped item
    assert mh.suggest("Second Breakfast", MAPPING) is None                            # already mapped
    assert mh.suggest("Pipe-weed", MAPPING) is None


def test_families_group_one_envelope_split_across_canonical_names():
    fams = mh.families(MAPPING)
    assert [f["base"] for f in fams] == ["Second Breakfast"]   # Mathom* already share one name; pass-through money is left alone on purpose
    f = fams[0]
    assert f["canonicals"] == ["Elevenses", "Second Breakfast"] and f["suggested"] == "Second Breakfast"
    assert [i["item"] for i in f["items"]] == ["Second Breakfast", "Second Breakfast 2", "Second Breakfast Fund"]


def test_family_suggestion_without_the_base_name_is_the_majority_name():
    rows = [{"item": i, "canonical_item": c, "category": "Gifts", "flow": "expense"} for i, c in
            [("Party Tree 2", "Party"), ("Party Tree 3", "Party"), ("Party Tree Fund", "Tree")]]
    assert mh.families(rows)[0]["suggested"] == "Party"


@pytest.mark.parametrize("item, group, flow", [
    ("Fund Transfer", "Income", "transfer"), ("Transfer Funds", "Savings", "transfer"), ("Year End Rebalance", "Income", "transfer"),
    ("Transfer to Long Term Savings", "Savings", "transfer"), ("Transfer Fees", "Banking", "expense"),   # a fee is spending, not a move
    ("Rosie Paycheck", "Income", "income"), ("Student Loan", "Debt", "debt"), ("Pipe-weed", "Lifestyle", "expense")])
def test_flow_for(item, group, flow):
    assert mh.flow_for(item, group) == flow


def test_transfer_names_are_a_rule():
    assert mh.flow_for("Envelope Shuffle", "Income", {"transfer_item_names": "envelope shuffle"}) == "transfer"
    assert mh.flow_for("Fund Transfer", "Income", {"transfer_item_names": "envelope shuffle"}) == "income"


def test_draft_adds_only_missing_items_and_keeps_one_name_per_envelope():
    unmapped = [{"item": "Second Breakfast", "group": "Food"},   # already mapped: never drafted
                {"item": "Second Breakfast 3", "group": "Meals"}, {"item": "Pipe-weed 2", "group": "Lifestyle"},
                {"item": "Pipe-weed Fund", "group": "Lifestyle"}, {"item": "Fund Transfer", "group": "Income"}, {"item": "Fund Transfer", "group": "Income"}]
    d = {r["item"]: r for r in mh.draft(unmapped, MAPPING)}
    assert list(d) == ["Second Breakfast 3", "Pipe-weed 2", "Pipe-weed Fund", "Fund Transfer"]
    assert d["Second Breakfast 3"] == {"item": "Second Breakfast 3", "canonical_item": "Second Breakfast", "category": "Food", "flow": "expense",
                                       "note": "drafted: like Second Breakfast"}   # the household's own decision wins over the group
    assert d["Pipe-weed 2"]["canonical_item"] == d["Pipe-weed Fund"]["canonical_item"] == "Pipe-weed"
    assert d["Pipe-weed 2"]["category"] == "Lifestyle" and d["Pipe-weed 2"]["note"] == "drafted from group Lifestyle"
    assert d["Fund Transfer"]["flow"] == "transfer" and d["Fund Transfer"]["canonical_item"] == "Fund Transfer"


# ------------------------------------------------------------------ routes

def mapping(conn):
    return {r["item"]: dict(r) for r in conn.execute("SELECT item, canonical_item, category, flow, note FROM mapping_rows").fetchall()}


@pytest.fixture
def two_unmapped(client, reset_db):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM item_mapping WHERE item IN ('Holiday', 'Groceries')"); conn.commit()
    upload_files(client, GOLDEN / "exports")
    with db.pool.connection() as conn:
        return mapping(conn)


def test_draft_page_lists_the_unmapped_items_and_saves_only_on_add(client, two_unmapped):
    page = client.get("/settings").text
    assert 'id="draft-offer"' in page and "2 items in your exports have no mapping row" in page
    page = client.get("/settings/mapping/draft").text
    assert "Holiday" in page and "Groceries" in page and "drafted from group" in page
    with db.pool.connection() as conn:
        assert mapping(conn) == two_unmapped   # showing the draft saved nothing
    body = page.split('name="body" spellcheck="false">')[1].split("</textarea>")[0]
    import html
    body = html.unescape(body)
    rows = list(csv.DictReader(io.StringIO(body)))
    assert {r["Item"] for r in rows} == {"Holiday", "Groceries"}
    r = client.post("/settings/mapping/draft", data={"body": body})
    assert r.status_code == 303 and r.headers["location"] == "/settings#mapping"
    with db.pool.connection() as conn:
        now = mapping(conn)
    assert {k: v for k, v in now.items() if k not in ("Holiday", "Groceries")} == two_unmapped   # no existing row changed
    assert now["Groceries"]["flow"] == "expense" and now["Groceries"]["note"].startswith("drafted")
    assert "Every item in your exports already has a mapping row" in client.get("/settings/mapping/draft").text


def test_draft_refuses_an_item_already_mapped_and_saves_nothing(client, two_unmapped):
    body = "Item,CanonicalItem,Category,Flow,Note\nHoliday,Holiday,Savings,expense,\nRent,Rent,Housing,debt,\n"
    page = client.get(client.post("/settings/mapping/draft", data={"body": body}).headers["location"]).text
    assert "Not saved" in page and "line 3: item already in the mapping on: Rent" in page
    with db.pool.connection() as conn:
        assert mapping(conn) == two_unmapped


def test_family_fix_changes_only_the_canonical_name(client, reset_db):
    for item, canon in (("Second Breakfast", "Second Breakfast"), ("Second Breakfast 2", "Elevenses")):
        assert client.post("/settings/mapping/row", data={"item": item, "canonical_item": canon, "category": "Food", "flow": "expense", "note": item}).json()["ok"]
    page = client.get("/settings").text
    assert 'id="name-families"' in page and "Second Breakfast 2 → <code>Elevenses</code>" in page
    r = client.post("/settings/mapping/family", data={"base": "Second Breakfast", "canonical_item": "Second Breakfast"})
    assert r.status_code == 303
    with db.pool.connection() as conn:
        m = mapping(conn)
    assert m["Second Breakfast 2"] == {"item": "Second Breakfast 2", "canonical_item": "Second Breakfast", "category": "Food", "flow": "expense", "note": "Second Breakfast 2"}
    assert 'id="name-families"' not in client.get("/settings").text
    page = client.get(client.post("/settings/mapping/family", data={"base": "Second Breakfast", "canonical_item": "X"}).headers["location"]).text
    assert "Nothing changed" in page


def test_suggest_endpoint(client, reset_db):
    assert client.get("/settings/mapping/suggest", params={"item": "Emergency Fund 2"}).json()["suggestion"]["canonical_item"] == "Emergency Fund"
    assert client.get("/settings/mapping/suggest", params={"item": "Pipe-weed"}).json() == {"suggestion": None}


def test_build_log_guesses_use_the_existing_mapping(client, two_unmapped):
    with db.pool.connection() as conn:
        from pipeline import mapping_store
        mapping_store.write_rows(conn, [{"item": "Holiday 2", "canonical_item": "Holiday Fund", "category": "Savings", "flow": "expense", "note": ""}]); conn.commit()
    from app.routes.findings import latest_findings
    from app.builds import run_background
    run_background()
    with db.pool.connection() as conn:
        un = {u["item"]: u for u in latest_findings(conn)["unmapped"]}
    assert un["Holiday"]["canonical_guess"] == "Holiday Fund" and un["Holiday"]["category_guess"] == "Savings"
